# -*- coding: utf-8 -*-
"""语音“考官”：用系统声音(扬声器)朗读面试问题，供 AI 面试助手实时抓取并作答。

为保证识别准确，采用“SAPI 渲染成 WAV → 用 soundcard 播放到默认扬声器”这条路径
（实测比 SAPI 直接朗读到设备清晰得多，后者会被回环抓成乱码）。

用法：
  1) 先双击 run.bat 启动助手，等状态显示“聆听中…（模型已就绪）”。
  2) 再双击 speak_test.bat（或运行本脚本）。它会逐题朗读，每题之间留时间让助手作答。
命令行可选参数：
  --gap 35      每题朗读后停顿秒数(留给助手作答)，默认 35
  --delay 5     开始前倒计时秒数，方便切到助手窗口，默认 5
  --device 名称  指定播放到的扬声器(关键词匹配)，默认系统默认扬声器
"""
import os
import sys
import time
import wave
import tempfile
import argparse
import subprocess

import numpy as np

try:
    import soundcard as sc
except Exception:
    sc = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# —— 贴合“质量测试经理 / 逆向 / 项目经理”方向的面试题，可自行增删 ——
QUESTIONS = [
    "你怎么理解ehs岗位。",
   ]


def render_to_wav(text, path):
    """用 Windows SAPI 把一句话渲染成 WAV 文件。
    中文用 base64 传给 PowerShell，避开 GBK 控制台编码导致的乱码。"""
    import base64
    tb64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
    pb64 = base64.b64encode(path.encode("utf-8")).decode("ascii")
    ps = (
        "Add-Type -AssemblyName System.Speech;"
        "$t=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + tb64 + "'));"
        "$p=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + pb64 + "'));"
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "$v=$s.GetInstalledVoices()|"
        "Where-Object{$_.VoiceInfo.Culture.Name -like 'zh*'}|Select-Object -First 1;"
        "if($v){$s.SelectVoice($v.VoiceInfo.Name)};"
        "$s.SetOutputToWaveFile($p);$s.Speak($t);$s.Dispose()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        check=False,
    )


def load_wav(path):
    w = wave.open(path, "rb")
    fr = w.getframerate()
    n = w.getnframes()
    ch = w.getnchannels()
    pcm = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
    w.close()
    if ch > 1:
        pcm = pcm.reshape(-1, ch).mean(axis=1)
    return pcm, fr


def pick_speaker(name):
    if name:
        for s in sc.all_speakers():
            if name.lower() in s.name.lower():
                return s
    return sc.default_speaker()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gap", type=int, default=35)
    ap.add_argument("--delay", type=int, default=5)
    ap.add_argument("--device", default="")
    ap.add_argument("--once", default="")  # 只朗读这一句然后退出（独立进程用）
    args = ap.parse_args()

    if sc is None:
        print("缺少 soundcard 库，请先运行 setup.bat。")
        return

    spk = pick_speaker(args.device)

    if args.once:
        tmp = os.path.join(tempfile.gettempdir(), "_examiner_once.wav")
        render_to_wav(args.once, tmp)
        pcm, fr = load_wav(tmp)
        spk.play(pcm, samplerate=fr)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return

    print()
    print("=" * 56)
    print(f"  语音考官（共 {len(QUESTIONS)} 题，每题后停顿 {args.gap} 秒）")
    print(f"  播放设备：{spk.name}")
    print("=" * 56)
    print("请确认 AI 面试助手已在运行且显示『聆听中…（模型已就绪）』。")
    for i in range(args.delay, 0, -1):
        print(f"  {i} 秒后开始…")
        time.sleep(1)

    tmp = os.path.join(tempfile.gettempdir(), "_examiner_q.wav")
    for i, q in enumerate(QUESTIONS, 1):
        print(f"\n【第 {i}/{len(QUESTIONS)} 题】{q}")
        try:
            render_to_wav(q, tmp)
            pcm, fr = load_wav(tmp)
            spk.play(pcm, samplerate=fr)
        except Exception as e:
            print(f"  [朗读失败：{e}]")
        if i < len(QUESTIONS):
            print(f"  （停顿 {args.gap}s，等助手作答…）")
            time.sleep(args.gap)
    try:
        os.remove(tmp)
    except OSError:
        pass
    print("\n全部问题已朗读完毕。")


if __name__ == "__main__":
    main()
