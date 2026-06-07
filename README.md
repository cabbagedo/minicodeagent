# MiniCodeAgent

参考 [OpenHands](https://github.com/All-Hands-AI/OpenHands) 架构、用 Python 从零实现的**自主编程 Agent（coding agent）**。给它一个自然语言任务（如"修复这个 bug"），它会自主读写代码、在 Docker 沙箱中执行命令、根据结果迭代，直至完成。

## ✨ 核心特性

- **Agent 执行循环（ReAct）**：手写 think → act → observe 循环，基于 Function Calling 让模型自主选择工具、依据结果迭代，直至完成。
- **工具系统**：`read_file` / `edit_file` / `run_command` / `list_files`，以"工具名 → 函数"映射动态调度。
- **Docker 沙箱隔离**：命令在容器内执行，仅挂载工作目录 + 断网 + 限内存 512M，防止误操作影响主机。
- **防失控**：最大迭代轮数 + 连续相同操作的卡死检测 + 沙箱资源限制。
- **事件流（event sourcing）**：每步 Action/Observation 落为结构化事件，全过程可回放、可审计。
- **错误自纠正**：命令报错作为 Observation 喂回，agent 自我修正。

## 🏗 架构

```
任务 → run() 循环：
   想（调 LLM）→ 做（执行工具）→ 看（结果喂回）→ 再想 …
   run_command 的命令在 Docker 沙箱内执行
   每步记入 events.jsonl（事件流）
   模型不再调工具 → 完成
```

## 🚀 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 API Key（复制模板，填入你的 DeepSeek key）
cp .env.example .env

# 3. 确保本机已安装并启动 Docker

# 4. 运行（自动修复 workspace 里的 bug 示例）
python agent.py

# 5. 跑评估（5 个修 bug 任务）
python eval.py
```

## 📊 评估

在 5 个不同类型的修 bug 任务（阶乘边界 / 加减 / 语法错 / 求和 / max-min）上端到端测试，**修复成功率 100%（5/5）**。

## 📁 结构

| 文件 | 说明 |
|---|---|
| `agent.py` | Agent 主体：执行循环 + 工具 + Docker 沙箱 + 防失控 + 事件流 |
| `eval.py` | 5 任务评估脚本 |
| `workspace/` | Agent 的工作目录（示例文件） |

## 🙏 致谢

架构思路参考 [OpenHands](https://github.com/All-Hands-AI/OpenHands)，本项目为学习性质的最小复刻。
