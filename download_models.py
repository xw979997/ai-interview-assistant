# -*- coding: utf-8 -*-
"""下载本地离线方案所需的模型：
   1) 语言模型 Qwen2.5-3B-Instruct（GGUF, q4_k_m, ~1.9GB）
   2) 语音识别模型 faster-whisper（默认 small, ~0.5GB）
全部下载到 models/ 目录，下载完即可离线使用。
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(BASE_DIR, "models")

LLM_REPO = "Qwen/Qwen2.5-3B-Instruct-GGUF"
LLM_FILE = "qwen2.5-3b-instruct-q4_k_m.gguf"
WHISPER_SIZE = os.environ.get("WHISPER_SIZE", "small")


def download_llm():
    from huggingface_hub import hf_hub_download
    target = os.path.join(MODELS_DIR, LLM_FILE)
    if os.path.exists(target) and os.path.getsize(target) > 100 * 1024 * 1024:
        print(f"[1/2] 语言模型已存在，跳过：{target}")
        return
    os.makedirs(MODELS_DIR, exist_ok=True)
    print(f"[1/2] 下载语言模型 {LLM_FILE}（约 1.9GB，请耐心等待）…")
    hf_hub_download(repo_id=LLM_REPO, filename=LLM_FILE, local_dir=MODELS_DIR)
    print("      语言模型下载完成。")


def download_whisper():
    from faster_whisper import WhisperModel
    print(f"[2/2] 下载语音识别模型 faster-whisper：{WHISPER_SIZE} …")
    WhisperModel(
        WHISPER_SIZE,
        device="cpu",
        compute_type="int8",
        download_root=os.path.join(MODELS_DIR, "whisper"),
    )
    print("      语音识别模型下载完成。")


if __name__ == "__main__":
    download_llm()
    download_whisper()
    print("\n全部模型就绪，现在可以双击 run.bat 启动了（全程离线）。")
