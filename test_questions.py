# -*- coding: utf-8 -*-
"""一键测试：用预设的常见面试问题，检验本地 AI 面试助手对你简历的回答效果。

用法：双击 test.bat，或命令行 python test_questions.py
可在下面 QUESTIONS 列表里自由增删问题。
"""
import os
import sys
import time
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
import interview_assistant as ia

# —— 这里是预设的常见面试问题，已贴合「质量测试经理 / 逆向 / 项目经理」方向，可自行增删 ——
QUESTIONS = [
    "请你做一个简单的自我介绍。",
    "你为什么想从质量测试工程师走向质量测试经理这个岗位？你的职业规划是怎样的？",
    "请讲一个你做过的最有挑战的测试项目，遇到了什么困难，你是怎么解决的？",
    "遇到一个应用程序崩溃(Crash)或系统蓝屏(BSOD)的疑难缺陷，你会怎么做根因分析？会用到哪些工具？",
    "如果让你为一次 Windows 系统更新设计兼容性测试策略，你会怎么规划测试矩阵和优先级？",
    "你是怎么做缺陷优先级管理的？P0 级别的 Bug 你是怎么界定的？",
    "作为测试负责人，你是怎么带领团队完成敏捷转型、并提升交付效率的？",
    "你和印度、美国的跨国测试团队协作时遇到过哪些挑战，是如何解决的？",
    "说说你的优点和不足。",
    "你怎么理解“测试左移”和“测试右移”？在你的项目里是怎么落地的？",
]


def main():
    cfg = ia.load_config()
    ctx = ia.load_context()
    if not ctx:
        print("⚠ 没读到 context.txt（简历内容），回答会比较泛。")
    stop = threading.Event()

    print("正在加载本地模型并预热（首次约 1 分钟，请稍候）…")
    eng = ia.make_engine(cfg, lambda s: print("  ·", s))
    sysp = ia.build_system_prompt(ctx)
    eng.warmup(sysp)  # 干净预热（非流式），避免堵塞后续请求

    print("\n" + "=" * 64)
    print("           本地 AI 面试助手 · 预设问题测试")
    print("=" * 64)
    total = time.time()
    for i, q in enumerate(QUESTIONS, 1):
        print(f"\n【问题 {i}/{len(QUESTIONS)}】面试官：{q}\n")
        print("AI 建议回答：", end="", flush=True)
        t = time.time()
        for d in eng.answer_stream(sysp, q, stop):
            sys.stdout.write(d)
            sys.stdout.flush()
        print(f"\n  （用时 {time.time() - t:.0f}s）")
        print("-" * 64)
    print(f"\n全部 {len(QUESTIONS)} 题测试完成，总用时 {time.time() - total:.0f}s。")


if __name__ == "__main__":
    main()
