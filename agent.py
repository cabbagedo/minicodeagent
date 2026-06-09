"""
MiniCodeAgent · 第 5 步:上下文管理(逼近工业级 coding agent)
================================================================
第 3 步:Docker 沙箱隔离;第 4 步:事件流 + 卡死检测(防失控)。
本步新增「上下文管理」,解决长任务把对话历史堆爆模型上下文窗口
(deepseek-chat 约 64K token)、导致 API 报错 / agent 崩溃的问题:
  - 上下文压缩器 Condenser:历史超过阈值时,用 LLM 把早期回合总结成「进展摘要」,
    只保留 系统+初始任务 和 最近几个回合(= OpenHands 的 LLMSummarizingCondenser)
  - 超长输出截断:单条命令/工具输出过长时只留头尾,防止一条 observation 就撑爆上下文
运行: venv/bin/python agent.py
"""

import json
import os

import docker
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
client = OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com")

WORKSPACE = os.path.join(os.path.dirname(__file__), "workspace")
EVENTS_PATH = os.path.join(os.path.dirname(__file__), "events.jsonl")


# ========== 事件流(event sourcing,= OpenHands 事件系统) ==========
def log_event(step, kind, data):
    """把每个动作/观察记成一条事件,可回放、可审计"""
    event = {"step": step, "kind": kind, "data": data}
    with open(EVENTS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


# ========== Docker 沙箱(= OpenHands DockerSandboxService) ==========
class DockerSandbox:
    def __init__(self, workspace, image="python:3.12-slim"):
        self.docker = docker.from_env()
        print(f"🏠 启动沙箱容器（{image}）…")
        self.container = self.docker.containers.run(
            image, command="sleep infinity",
            volumes={workspace: {"bind": "/workspace", "mode": "rw"}},  # 文件隔离
            working_dir="/workspace", detach=True,
            mem_limit="512m",      # 资源限制(防失控之一)
            network_mode="none",   # 断网
        )

    def exec(self, command):
        res = self.container.exec_run(["sh", "-c", command], workdir="/workspace")
        return res.output.decode("utf-8", errors="replace") or "(无输出)"

    def cleanup(self):
        self.container.stop()
        self.container.remove()
        print("🧹 沙箱已清理")


sandbox = None


# ========== 工具 ==========
def list_files(path="."):
    return "\n".join(os.listdir(os.path.join(WORKSPACE, path)))


def read_file(filename):
    with open(os.path.join(WORKSPACE, filename), encoding="utf-8") as f:
        return f.read()


def edit_file(filename, old_str, new_str):
    """局部替换:把文件里的 old_str 改成 new_str(只改片段,不重写整个文件)。
    old_str 必须在文件中精确且唯一匹配;找不到 / 匹配多处都返回错误,
    让模型据此补更多上下文重试(失败信息会作为 observation 回喂模型)。"""
    path = os.path.join(WORKSPACE, filename)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    count = text.count(old_str)
    if count == 0:
        return f"错误:在 {filename} 里没找到 old_str(注意缩进/空格要完全一致),请核对后重试。"
    if count > 1:
        return f"错误:old_str 在 {filename} 里匹配到 {count} 处、不唯一,请在 old_str 多带几行上下文以唯一定位。"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.replace(old_str, new_str))
    return f"已修改 {filename}:替换了 1 处"


def run_command(command):
    return sandbox.exec(command)


TOOLS = [
    {"type": "function", "function": {
        "name": "list_files", "description": "列出工作目录下有哪些文件",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "相对目录,默认当前"}}}}},
    {"type": "function", "function": {
        "name": "read_file", "description": "读取某个文件的全部内容",
        "parameters": {"type": "object", "properties": {
            "filename": {"type": "string"}}, "required": ["filename"]}}},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": "局部修改文件:把 old_str 精确替换成 new_str(只改片段、不重写整个文件)。"
                       "old_str 必须与文件中目标片段完全一致(含缩进)且在文件中唯一;"
                       "若找不到或匹配多处会报错,需多带上下文重试。",
        "parameters": {"type": "object", "properties": {
            "filename": {"type": "string"},
            "old_str": {"type": "string", "description": "要被替换的原始片段(需与文件内容完全一致且唯一)"},
            "new_str": {"type": "string", "description": "替换后的新片段"}},
            "required": ["filename", "old_str", "new_str"]}}},
    {"type": "function", "function": {
        "name": "run_command",
        "description": "在 Docker 沙箱里执行 shell 命令(如 python xxx.py),返回输出和报错",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"}}, "required": ["command"]}}},
]
TOOL_FUNCS = {"list_files": list_files, "read_file": read_file,
              "edit_file": edit_file, "run_command": run_command}


# ========== 上下文管理(= OpenHands 的 Condenser + 输出截断) ==========
MAX_OBS_CHARS = 4000  # 单条工具/命令输出喂给模型的字符上限


def clip_observation(text):
    """单条 observation 过长时只保留头尾,防止一条输出(如长日志)就撑爆上下文窗口。"""
    if len(text) <= MAX_OBS_CHARS:
        return text
    half = MAX_OBS_CHARS // 2
    omitted = len(text) - MAX_OBS_CHARS
    return f"{text[:half]}\n…(输出过长,中间省略 {omitted} 字)…\n{text[-half:]}"


class Condenser:
    """上下文压缩器(= OpenHands 的 LLMSummarizingCondenser)。
    对话历史变长、逼近模型上下文窗口(deepseek-chat 约 64K token)时,把中间的旧历史
    用 LLM 总结成一段「进展摘要」,只保留 系统+初始任务 和 最近几个回合,中段换成摘要,
    防止长任务把 messages 堆爆上下文窗口导致 API 报错。"""

    def __init__(self, client, max_turns=8, keep_recent=3):
        self.client = client
        self.max_turns = max_turns      # 历史超过这么多「回合」就触发压缩
        self.keep_recent = keep_recent  # 压缩时原样保留最近几个回合

    @staticmethod
    def _role(m):
        return m["role"] if isinstance(m, dict) else m.role

    def _split_turns(self, messages):
        """把平铺 messages 切成 head(系统+初始任务) + 若干「回合」。
        每个回合 = 1 条 assistant + 其后跟随的 tool 结果,自包含不可拆,
        这样压缩绝不会拆散 tool_calls 与 tool 响应的配对(否则 API 报错)。"""
        head = messages[:2]
        turns, cur = [], []
        for m in messages[2:]:
            if self._role(m) == "assistant":
                if cur:
                    turns.append(cur)
                cur = [m]
            else:
                cur.append(m)
        if cur:
            turns.append(cur)
        return head, turns

    def condense(self, messages, step):
        head, turns = self._split_turns(messages)
        if len(turns) <= self.max_turns:
            return messages  # 没到阈值,原样返回(小任务永远走这条)
        old, recent = turns[:-self.keep_recent], turns[-self.keep_recent:]
        summary = self._summarize(old)
        log_event(step, "condense", {"compressed_turns": len(old), "kept_turns": len(recent)})
        print(f"🗜️  上下文压缩:早期 {len(old)} 个回合 → 摘要,保留最近 {len(recent)} 个回合")
        summary_msg = {"role": "user",
                       "content": f"【历史进展摘要(前 {len(old)} 个回合已压缩,据此继续)】\n{summary}"}
        return head + [summary_msg] + [m for t in recent for m in t]

    def _summarize(self, old_turns):
        """把中段历史拍平成文本,让 LLM 浓缩成要点。"""
        lines = []
        for t in old_turns:
            for m in t:
                if self._role(m) == "assistant":
                    content = m.content if not isinstance(m, dict) else m.get("content")
                    if content:
                        lines.append(f"[思考] {content}")
                    for tc in (getattr(m, "tool_calls", None) or []):
                        lines.append(f"[动作] {tc.function.name}({tc.function.arguments[:200]})")
                else:  # tool 结果
                    c = m["content"] if isinstance(m, dict) else m.content
                    lines.append(f"[结果] {c[:300]}")
        resp = self.client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是上下文压缩器,把编程 agent 的冗长历史浓缩成简明要点。"},
                {"role": "user", "content":
                    "请把下面的 agent 历史压缩成进展摘要,务必保留:读过哪些文件、定位到什么 bug、"
                    "做了哪些修改、命令/报错的关键信息,让 agent 能据此无缝继续任务。\n\n"
                    + "\n".join(lines)},
            ],
        )
        return resp.choices[0].message.content


# ========== think-act-observe 循环 ==========
def run(task, max_steps=15):
    open(EVENTS_PATH, "w").close()  # 每次运行清空事件日志
    messages = [
        {"role": "system", "content":
            "你是一个编程助手,可以用工具读文件、改文件、在沙箱里跑命令来完成任务。"
            "修 bug 的标准流程:先读代码 → 跑一次看现象 → 定位 → 改 → 再跑一次验证。"
            "确认任务完成后直接用文字回答,不要再调用工具。"},
        {"role": "user", "content": task},
    ]
    recent_sigs = []  # 最近的操作签名,用于卡死检测
    condenser = Condenser(client)  # 上下文压缩器:防止长任务堆爆上下文窗口

    for step in range(1, max_steps + 1):  # 防失控①:最大轮数
        print(f"\n===== 第 {step} 轮 =====")
        messages = condenser.condense(messages, step)  # 上下文管理:逼近窗口时压缩旧历史
        resp = client.chat.completions.create(
            model="deepseek-chat", messages=messages, tools=TOOLS
        )
        msg = resp.choices[0].message
        messages.append(msg)

        if not msg.tool_calls:  # 完成(FinishTool)
            log_event(step, "finish", {"answer": msg.content})
            print("✅ Agent 最终回答:", msg.content)
            return msg.content

        for call in msg.tool_calls:
            name = call.function.name
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                result = "错误:arguments 不是合法 JSON,请重新生成这次工具调用"
                log_event(step, "observation", {"tool": name, "result": result})
                print(f"👀 {result}")
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
                continue

            # 防失控②:卡死检测——连续 3 次完全相同的操作就停
            sig = f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
            recent_sigs.append(sig)
            if len(recent_sigs) >= 3 and len(set(recent_sigs[-3:])) == 1:
                print("🛑 卡死检测:连续 3 次相同操作,提前停止(防失控)")
                log_event(step, "stuck", {"signature": sig})
                return None

            log_event(step, "action", {"tool": name, "args": args})  # 事件:动作
            print(f"🔧 {name}({ {k: str(v)[:50] for k, v in args.items()} })")
            try:
                result = TOOL_FUNCS[name](**args)
            except Exception as e:
                result = f"工具出错:{e}"
            log_event(step, "observation", {"tool": name, "result": result[:500]})  # 事件:观察
            print(f"👀 {result[:200]}")
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": clip_observation(result)})  # 截断超长输出,防爆上下文

    print("⚠️ 达到最大轮数,强制停止")


if __name__ == "__main__":
    sandbox = DockerSandbox(WORKSPACE)
    try:
        run("workspace 里的 calc.py 计算阶乘,5! 应该等于 120,但运行结果不对。"
            "请定位并修复 bug,确保运行后输出正确的 120。")
    finally:
        sandbox.cleanup()
