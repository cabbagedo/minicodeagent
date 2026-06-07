"""
评估 MiniCodeAgent:造 5 个不同的 bug 任务,逐个让 agent 修,自动验证并统计成功率。
运行: venv/bin/python eval.py
"""

import os

import agent  # 复用 agent.py 里的 run / DockerSandbox / 工具
from agent import WORKSPACE, DockerSandbox, run

TASKS = [
    {"file": "fact.py",
     "buggy": "def f(n):\n    result = 1\n    for i in range(1, n):\n        result *= i\n    return result\n\nprint(f(5))\n",
     "task": "fact.py 计算阶乘，f(5) 应该输出 120，但结果不对，请修复。",
     "expect": "120"},
    {"file": "add.py",
     "buggy": "def add(a, b):\n    return a - b\n\nprint(add(2, 3))\n",
     "task": "add.py 里 add(2,3) 应该是加法、输出 5，但写错了，请修复。",
     "expect": "5"},
    {"file": "hello.py",
     "buggy": 'print("Hello, World"\n',
     "task": "hello.py 有语法错误跑不起来，请修好，让它输出 Hello, World。",
     "expect": "Hello, World"},
    {"file": "sum10.py",
     "buggy": "total = 0\nfor i in range(1, 10):\n    total += i\nprint(total)\n",
     "task": "sum10.py 应该计算 1 到 10 的和（=55），但结果不对，请修复。",
     "expect": "55"},
    {"file": "maxnum.py",
     "buggy": "nums = [3, 7, 2, 8, 5]\nprint(min(nums))\n",
     "task": "maxnum.py 应该输出列表的最大值（=8），但结果不对，请修复。",
     "expect": "8"},
]


def main():
    agent.sandbox = DockerSandbox(WORKSPACE)  # 5 个任务共用一个沙箱
    passed = 0
    results = []
    try:
        for i, t in enumerate(TASKS, 1):
            print(f"\n########## 任务 {i}/{len(TASKS)}: {t['file']} ##########")
            # 写入有 bug 的文件
            with open(os.path.join(WORKSPACE, t["file"]), "w", encoding="utf-8") as f:
                f.write(t["buggy"])
            # 让 agent 修
            run(t["task"])
            # 验证:在沙箱里跑修后的文件,看输出是否含期望值
            output = agent.sandbox.exec(f"python {t['file']}").strip()
            ok = t["expect"] in output
            passed += ok
            results.append((t["file"], ok, output))
            print(f"——— 验证 {t['file']}: 期望含 '{t['expect']}'，实际='{output}' → {'✅ 通过' if ok else '❌ 失败'}")
    finally:
        agent.sandbox.cleanup()

    print("\n========== 评估汇总 ==========")
    for name, ok, out in results:
        print(f"  {'✅' if ok else '❌'} {name:12s} 输出={out}")
    print(f"\n🎯 成功率: {passed}/{len(TASKS)} = {passed / len(TASKS) * 100:.0f}%")


if __name__ == "__main__":
    main()
