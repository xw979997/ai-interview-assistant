# -*- coding: utf-8 -*-
"""下载语音识别模型 faster-whisper 到 models/ 目录。
（语言模型由 Ollama 提供，用 `ollama pull` 获取，不在这里下载。）
可用环境变量 WHISPER_SIZE 指定大小，默认 base（与 config.json 一致）。
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(BASE_DIR, "models")
WHISPER_SIZE = os.environ.get("WHISPER_SIZE", "base")


def download_whisper():
    from faster_whisper import WhisperModel
    print(f"下载语音识别模型 faster-whisper：{WHISPER_SIZE} …")
    WhisperModel(
        WHISPER_SIZE,
        device="cpu",
        compute_type="int8",
        download_root=os.path.join(MODELS_DIR, "whisper"),
    )
    print("语音识别模型下载完成。")


if __name__ == "__main__":
    download_whisper()
    print("\n语音模型就绪。语言模型请用 setup.bat 里的 `ollama pull` 获取。")
