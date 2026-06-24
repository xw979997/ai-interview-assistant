# -*- coding: utf-8 -*-
"""
AI 面试助手 / AI Interview Assistant —— 本地离线版（默认无需联网、无需 API Key）

工作流：
  1. 用 WASAPI loopback 捕获“系统输出声音”（面试官通过你扬声器/耳机说的话）
  2. 简单能量 VAD 把语音切成一句一句
  3. 本地 faster-whisper 把语音转成中文文字（问题）
  4. 本地 llama.cpp（Qwen2.5 中文模型）流式生成可以直接照着说的中文答案
  5. Tkinter 置顶悬浮窗实时显示问题和答案

backend=local 时全程离线、免费；也可在 config.json 把 backend 改成 openai 走云端。
配置见 config.json，简历/岗位资料见 context.txt。
"""

import os
import io
import json
import wave
import queue
import threading
import traceback
import tkinter as tk
from tkinter import scrolledtext, font, ttk

import numpy as np

try:
    import soundcard as sc
except Exception:  # pragma: no cover
    sc = None


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
CONTEXT_PATH = os.path.join(BASE_DIR, "context.txt")
SAMPLE_RATE = 16000
FRAME_SECONDS = 0.1  # 每帧 100ms

DEFAULT_CONFIG = {
    "backend": "local",                 # local 或 openai
    "language": "zh",

    # 本地（local）相关：faster-whisper 语音识别 + Ollama 本地大模型
    "whisper_model": "base",            # tiny / base / small / medium
    "whisper_compute": "int8",
    "threads": 0,                       # 0 = 自动
    "max_tokens": 170,
    "temperature": 0.4,                 # 略升以让答案更丰富；提示词已锁住简历事实
    "ollama_url": "http://localhost:11434",
    "ollama_model": "qwen2.5:1.5b-instruct",

    # 云端（openai）相关
    "openai_api_key": "",
    "openai_base_url": "",
    "openai_transcribe_model": "whisper-1",
    "openai_answer_model": "gpt-4o-mini",

    # VAD / 采集
    "silence_seconds": 0.8,
    "max_utterance_seconds": 20,
    "min_utterance_seconds": 0.6,
    "energy_threshold": 0.0,
    "loopback_device": "",

    # 回答模式：latest=只答最新(跳过积压)；all=每题都答(按顺序逐题作答)
    "answer_mode": "latest",

    # 目标岗位（可选）：若你面的岗位与简历方向不同，填上岗位名，
    # 助手会优先按该岗位的专业知识作答、检索也更聚焦；留空则跟着问题本身走。
    "target_role": "",

    # 个人题作答时喂给模型的简历最大字数（默认很大=不截断，保证不丢简历末尾的
    # 学历等关键事实；简历特别长又想更快时可调小，但注意可能丢掉靠后的内容）
    "resume_max_chars": 8000,

    # 联网检索（可选）：每题先搜相关案例/资料，再结合作答（需联网，略慢）
    "web_search": True,
    "web_results": 2,
    "web_timeout": 6,
    "web_region": "cn-zh",
    "web_query_hint": "",
}


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:
            print(f"读取 config.json 失败，使用默认配置: {e}")
    if not cfg.get("openai_api_key"):
        cfg["openai_api_key"] = os.environ.get("OPENAI_API_KEY", "")
    return cfg


def save_config_value(key, value):
    """把单个配置项写回 config.json（用于界面里改动的持久化，失败不影响运行）。"""
    try:
        data = {}
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        data[key] = value
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_context():
    if os.path.exists(CONTEXT_PATH):
        try:
            with open(CONTEXT_PATH, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            return ""
    return ""


def condense_context(context, max_chars=3000):
    """把过长的简历精简到 max_chars 以内（在换行处截断），
    保留最有用的开头部分（个人信息+亮点+工作经历），
    既能覆盖个人题作答，又能显著加快模型处理速度。"""
    if not context or len(context) <= max_chars:
        return context
    cut = context[:max_chars]
    nl = cut.rfind("\n")
    if nl > max_chars * 0.6:
        cut = cut[:nl]
    return cut.rstrip() + "\n（以上为简历要点摘录）"


def build_system_prompt(target_role=""):
    base = (
        "你是一名顶尖的中文面试辅助助手。面试官会提问，"
        "你要以候选人第一人称（用“我”）给出可以直接照着读出来的中文回答。核心原则：\n"
        "1) 首要目标是把【这个问题本身】答得准确、专业、有深度——"
        "不论问题属于哪个行业或岗位，都要像该领域的资深专家那样作答，"
        "给出正确的概念、方法、流程、工具和真实案例；\n"
        "2) 先给结论，再展开 2-3 个要点；技术/专业问题给准确具体的方案，"
        "行为类问题用 STAR 结构（情境-任务-行动-结果）；\n"
        "3) 口语化、自然，像真人面试那样流畅地说，少堆“1、2、3”长串分点，"
        "不要使用 markdown（不要出现 #、*、### 等符号），就用大白话连贯地说，"
        "控制在 40 秒左右能说完（约 250 字以内）；\n"
        "4) 只输出能直接说出口的内容，不要写“面试官您好”之类客套，"
        "不要说明你是 AI，不要加解释性旁白；\n"
        "5) 关于简历：只有当用户消息里【明确附上了简历资料】时，才表示这是一道"
        "问你个人经历的题，要结合那份简历来回答（个人硬信息如学校、公司、职务、"
        "年限等要严格按简历、不得编造）；如果用户消息里【没有附简历】，"
        "就说明这道题问的是专业知识或某个岗位，"
        "请纯粹按该问题所属领域的专业知识作答，不要编造或硬扯任何个人经历。"
    )
    if target_role:
        # 放在最末尾并加重语气：小模型对靠后的、强调过的指令更敏感
        base += (
            f"\n\n★最重要的指令★ 本次面试的目标岗位是【{target_role}】。"
            f"请完全站在「{target_role}」的角度作答：\n"
            f"- 用「{target_role}」这个岗位的专业知识、术语、方法论和案例来回答；\n"
            "- 问题措辞宽泛时（如“优先级管理”“你怎么做规划”），"
            f"一律按「{target_role}」的语境去理解；\n"
            "- 个人硬信息（学校、年限等）若提及仍要真实，不得编造。"
        )
    return base


# 这些措辞说明面试官在问候选人本人的经历/背景，此时才把简历喂给模型
PERSONAL_TRIGGERS = (
    "自我介绍", "介绍一下自己", "介绍下自己", "介绍一下你", "介绍下你", "你自己",
    "你的优势", "你的优点", "你的缺点", "你的不足", "最大的优点", "最大的缺点",
    "你的项目", "项目经历", "项目经验", "你做过", "你负责", "你参与", "你主导",
    "你的经验", "你的经历", "你的背景", "你擅长", "你的技能", "你掌握", "你会哪些",
    "为什么选择", "为什么想", "为什么离职", "离职原因", "职业规划", "职业发展",
    "期望薪资", "谈谈你", "说说你", "讲讲你", "你过去", "你之前", "你目前",
    "你现在", "评价一下自己", "你为什么来", "你的故事", "你有什么经验",
)


def is_personal_question(question):
    """判断是否在问候选人本人的经历/背景（这类题才需要结合简历）。"""
    q = question or ""
    return any(t in q for t in PERSONAL_TRIGGERS)


def build_user_message(question, context, snippets, personal):
    """组装发给模型的用户消息：
    - 个人经历题：把简历附上，让模型结合简历回答；
    - 其他题：完全不放简历，只放问题（+联网资料），避免被简历带偏。"""
    parts = []
    if personal and context:
        parts.append(
            "【这是一道问我个人经历/背景的题，以下是我的真实简历资料，"
            "请严格基于它来回答，个人硬信息不得编造】\n" + context + "\n"
        )
    parts.append("问题：" + question)
    msg = "\n".join(parts)
    return augment_question(msg, snippets)


# --------------------------------------------------------------------------- #
# 联网检索（可选）：每题先搜相关案例/资料，再结合简历作答
# --------------------------------------------------------------------------- #
class WebSearch:
    def __init__(self, cfg):
        self.enabled = bool(cfg.get("web_search", False))
        self.n = int(cfg.get("web_results", 3))
        self.timeout = float(cfg.get("web_timeout", 12))
        self.region = cfg.get("web_region", "cn-zh")
        # 检索提示词：默认跟着问题走（不绑定任何固定行业），
        # 如果你明确知道要面的岗位，可在 config 的 target_role 写上，让检索更聚焦该岗位。
        self.hint = (cfg.get("web_query_hint") or "").strip()
        self.role = (cfg.get("target_role") or "").strip()

    def search(self, question):
        """返回若干条联网检索摘要（失败/超时则返回空串，绝不影响作答）。
        用线程加硬性超时：ddgs 自带的 timeout 只管单次 HTTP，整体可能拖很久，
        这里超过 web_timeout 秒就直接放弃，绝不拖累作答。"""
        if not self.enabled:
            return ""
        result = {"text": ""}

        def _run():
            try:
                result["text"] = self._do_search(question)
            except Exception:
                result["text"] = ""

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(self.timeout)
        # 超时则线程被丢弃（daemon，不阻塞），返回已有结果（多半为空）
        return result["text"]

    def _do_search(self, question):
        core = (question or "").strip().strip("？?。.！!")
        # 查询以“问题本身”为主，可选地附上目标岗位/自定义关键词，让结果更准。
        parts = [core]
        if self.role:
            parts.append(self.role)
        if self.hint:
            parts.append(self.hint)
        parts.append("面试 案例 最佳实践")
        query = " ".join(p for p in parts if p)
        from ddgs import DDGS
        lines = []
        with DDGS(timeout=self.timeout) as d:
            for r in d.text(query, region=self.region, max_results=self.n):
                title = (r.get("title") or "").strip()
                body = (r.get("body") or "").strip()
                if body:
                    lines.append(f"- {title}：{body}")
                if len(lines) >= self.n:
                    break
        return "\n".join(lines)


def augment_question(question, snippets):
    """把联网检索到的资料拼到问题后面，供模型结合作答。"""
    if not snippets:
        return question
    return (
        question
        + "\n\n【以下是联网检索到的相关资料，请有选择地结合其中有价值的观点、"
        "做法或案例来充实你的回答，但不要照抄、不要罗列来源，"
        "也不要据此编造我简历里没有的个人经历】\n"
        + snippets
    )


# --------------------------------------------------------------------------- #
# 音频工具
# --------------------------------------------------------------------------- #
def float_to_wav_bytes(audio, rate=SAMPLE_RATE):
    a = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
    pcm = (a * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    buf.seek(0)
    return buf.read()


def pick_loopback_mic(cfg):
    """返回一个可用于 loopback 录音的 soundcard microphone 对象（按配置）。"""
    return pick_loopback_mic_by_name((cfg.get("loopback_device") or "").strip())


def pick_loopback_mic_by_name(name):
    """按设备名关键词返回 loopback microphone；name 为空则用默认扬声器。"""
    if sc is None:
        raise RuntimeError("缺少 soundcard 库，请先运行 setup.bat 安装依赖。")
    name = (name or "").strip()
    if name:
        for m in sc.all_microphones(include_loopback=True):
            if name.lower() in m.name.lower():
                return m
        # 名字没匹配上时，退回默认扬声器，避免直接报错
    speaker = sc.default_speaker()
    return sc.get_microphone(id=str(speaker.name), include_loopback=True)


def list_output_devices():
    """枚举可回环抓取的输出设备，返回 [(显示名, 匹配名), ...]，首项为默认。"""
    devices = [("🔊 默认设备（自动跟随系统）", "")]
    if sc is None:
        return devices
    try:
        default_name = sc.default_speaker().name
    except Exception:
        default_name = None
    seen = set()
    try:
        for sp in sc.all_speakers():
            if sp.name in seen:
                continue
            seen.add(sp.name)
            label = sp.name
            if default_name and sp.name == default_name:
                label = "✓ " + label + "（系统默认）"
            devices.append((label, sp.name))
    except Exception:
        pass
    return devices


# --------------------------------------------------------------------------- #
# 推理引擎：语音识别(ASR) + 答案生成(LLM) 解耦
#   local  : faster-whisper 识别 + 本地 Ollama 大模型（离线、免费）
#   openai : OpenAI Whisper 识别 + GPT 生成（需联网、需 Key）
# --------------------------------------------------------------------------- #
class FasterWhisperASR:
    """本地 faster-whisper 语音识别（离线、免费）。"""

    def __init__(self, cfg, status_cb):
        self.language = cfg.get("language") or "zh"
        status_cb("加载语音识别模型中…")
        from faster_whisper import WhisperModel
        self.model = WhisperModel(
            cfg["whisper_model"],
            device="cpu",
            compute_type=cfg.get("whisper_compute", "int8"),
            cpu_threads=int(cfg.get("threads") or 0),
            download_root=os.path.join(BASE_DIR, "models", "whisper"),
        )

    def transcribe(self, audio_np):
        segments, _ = self.model.transcribe(
            audio_np,
            language=self.language or None,
            beam_size=1,
            condition_on_previous_text=False,
        )
        return "".join(s.text for s in segments).strip()


class OpenAIASR:
    """云端 OpenAI Whisper 语音识别（需要 api_key）。"""

    def __init__(self, client, cfg):
        self.client = client
        self.model = cfg["openai_transcribe_model"]
        self.language = cfg.get("language") or "zh"

    def transcribe(self, audio_np):
        wav = float_to_wav_bytes(audio_np, SAMPLE_RATE)
        r = self.client.audio.transcriptions.create(
            model=self.model,
            file=("audio.wav", wav, "audio/wav"),
            language=self.language or None,
        )
        return (getattr(r, "text", "") or "").strip()


class ChatLLM:
    """OpenAI 兼容的对话生成：既可指向 OpenAI 云端，也可指向本地 Ollama。"""

    def __init__(self, client, model, max_tokens=None, keep_alive=None,
                 temperature=0.3):
        self.client = client
        self.model = model
        self.max_tokens = max_tokens
        self.keep_alive = keep_alive  # 仅本地 Ollama：-1 表示常驻不卸载
        self.temperature = temperature

    def answer_stream(self, system_prompt, question, stop_event):
        kwargs = dict(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question},
            ],
            stream=True,
            temperature=self.temperature,
        )
        if self.max_tokens:
            kwargs["max_tokens"] = int(self.max_tokens)
        if self.keep_alive is not None:
            # Ollama 扩展参数：让模型在内存中常驻，避免空闲卸载后重载造成长时间等待
            kwargs["extra_body"] = {"keep_alive": self.keep_alive}
        stream = self.client.chat.completions.create(**kwargs)
        try:
            for chunk in stream:
                if stop_event.is_set():
                    break
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    # 去掉 markdown 噪声符号（* # `），口语面试照着念更顺
                    delta = delta.replace("*", "").replace("#", "").replace("`", "")
                    if delta:
                        yield delta
        finally:
            # 确保流被关闭，绝不把未读完的生成留在 Ollama 运行槽里堵塞后续请求
            try:
                stream.close()
            except Exception:
                pass

    def warmup(self, system_prompt, user_msg="你好"):
        """预热：非流式、只生成 1 个 token，干净结束。
        既把模型载入内存、缓存提示前缀，又不留挂起的生成堵塞后续请求。
        传入带简历的 user_msg 可顺便把简历前缀也缓存，让个人题首答也快。"""
        kwargs = dict(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            stream=False,
            temperature=0.0,
            max_tokens=1,
        )
        if self.keep_alive is not None:
            kwargs["extra_body"] = {"keep_alive": self.keep_alive}
        self.client.chat.completions.create(**kwargs)

    def classify_personal(self, question):
        """用模型快速判断这道题是否在问候选人本人（个人经历/背景/优缺点等）。
        返回 True=个人题（需要结合简历），False=专业/通用题（不放简历）。
        只生成 1 个汉字，很快；失败时返回 None 让调用方回退到关键词判断。"""
        sys_p = (
            "你是一个面试问题分类器。判断下面这句面试问题是哪一类，只回答一个汉字：\n"
            "“个” = 在问候选人【本人】的个人信息、经历、背景、技能、优缺点、"
            "求职动机、职业规划、薪资期望等（需要看候选人简历才能答）；\n"
            "“专” = 在考察某个领域的专业知识、技术原理或通用问题"
            "（不需要候选人简历，凭专业知识就能答）。\n"
            "只输出“个”或“专”这一个汉字，不要输出别的任何内容。"
        )
        try:
            kwargs = dict(
                model=self.model,
                messages=[
                    {"role": "system", "content": sys_p},
                    {"role": "user", "content": "问题：" + (question or "")},
                ],
                stream=False,
                temperature=0.0,
                max_tokens=3,
            )
            if self.keep_alive is not None:
                kwargs["extra_body"] = {"keep_alive": self.keep_alive}
            r = self.client.chat.completions.create(**kwargs)
            out = (r.choices[0].message.content or "")
            if "个" in out:
                return True
            if "专" in out:
                return False
            return None
        except Exception:
            return None


class Engine:
    """组合 ASR + LLM，对外暴露 transcribe / answer_stream。"""

    def __init__(self, asr, llm, closer=None):
        self.asr = asr
        self.llm = llm
        self.closer = closer  # 关闭时的清理回调（本地：释放 Ollama 显存/内存）

    def transcribe(self, audio_np):
        return self.asr.transcribe(audio_np)

    def answer_stream(self, system_prompt, question, stop_event):
        return self.llm.answer_stream(system_prompt, question, stop_event)

    def classify_personal(self, question):
        if hasattr(self.llm, "classify_personal"):
            return self.llm.classify_personal(question)
        return None

    def warmup(self, system_prompt, personal_user_msg=None):
        # 预热语言模型（载入内存 + 缓存系统提示）
        self.llm.warmup(system_prompt)
        # 顺便缓存“个人题（含简历）”的提示前缀，让自我介绍这类开场题首答也快
        if personal_user_msg:
            try:
                self.llm.warmup(system_prompt, personal_user_msg)
            except Exception:
                pass
        # 预热语音识别（首次转写会做一些初始化，先用极短静音跑一遍）
        try:
            self.asr.transcribe(np.zeros(SAMPLE_RATE // 2, dtype=np.float32))
        except Exception:
            pass

    def close(self):
        if self.closer:
            try:
                self.closer()
            except Exception:
                pass


def _ollama_set_keepalive(http_client, url, model, keep_alive):
    """通过 Ollama 原生接口设置模型常驻策略。
    keep_alive=-1 常驻不卸载；0 立即卸载释放内存。"""
    try:
        http_client.post(
            url + "/api/generate",
            json={"model": model, "keep_alive": keep_alive},
        )
    except Exception:
        pass


def _make_local_engine(cfg, status_cb):
    import httpx
    from openai import OpenAI
    asr = FasterWhisperASR(cfg, status_cb)

    url = (cfg.get("ollama_url") or "http://localhost:11434").rstrip("/")
    model = cfg.get("ollama_model") or "qwen-interview"
    status_cb("连接本地 Ollama…")
    # trust_env=False：绕过系统/环境代理，确保直连本机 Ollama（否则代理会返回 502）
    http_client = httpx.Client(
        trust_env=False, timeout=httpx.Timeout(90.0, connect=10.0)
    )
    client = OpenAI(base_url=url + "/v1", api_key="ollama", http_client=http_client)
    try:
        names = [m.id for m in client.models.list().data]
    except Exception as e:
        raise RuntimeError(
            "无法连接本地 Ollama 服务（" + url + "）。请确认 Ollama 已安装并在运行"
            "（从开始菜单启动 Ollama，或命令行执行 ollama serve）。原始错误：" + str(e)
        )
    if model not in names and not any(n.startswith(model) for n in names):
        raise RuntimeError(
            f"Ollama 里还没有模型“{model}”。请先运行 setup.bat。当前可用：{names}"
        )
    # 把模型钉在内存里（整场面试不卸载），避免空闲卸载后重载造成长时间“假死”
    status_cb("加载本地大模型到内存…")
    _ollama_set_keepalive(http_client, url, model, -1)
    closer = lambda: _ollama_set_keepalive(http_client, url, model, 0)
    return Engine(
        asr,
        ChatLLM(client, model, cfg.get("max_tokens"), keep_alive=-1,
                temperature=float(cfg.get("temperature", 0.3))),
        closer=closer,
    )


def _make_openai_engine(cfg, status_cb):
    from openai import OpenAI
    if not cfg.get("openai_api_key"):
        raise RuntimeError("backend=openai 但没有配置 openai_api_key。")
    kwargs = {"api_key": cfg["openai_api_key"]}
    if cfg.get("openai_base_url"):
        kwargs["base_url"] = cfg["openai_base_url"]
    client = OpenAI(**kwargs)
    return Engine(
        OpenAIASR(client, cfg),
        ChatLLM(client, cfg["openai_answer_model"],
                temperature=float(cfg.get("temperature", 0.3))),
    )


def make_engine(cfg, status_cb):
    if (cfg.get("backend") or "local").lower() == "openai":
        return _make_openai_engine(cfg, status_cb)
    return _make_local_engine(cfg, status_cb)


# --------------------------------------------------------------------------- #
# 采集 + VAD 线程
# --------------------------------------------------------------------------- #
class AudioListener(threading.Thread):
    def __init__(self, cfg, seg_queue, ui_queue, listening_event, stop_event):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.seg_queue = seg_queue
        self.ui_queue = ui_queue
        self.listening = listening_event
        self.stop = stop_event
        self._lock = threading.Lock()
        self._device_name = (cfg.get("loopback_device") or "").strip()
        self._reload = threading.Event()

    def set_device(self, name):
        """切换抓取的输出设备（name 为空=默认设备）。线程安全，立即生效。"""
        with self._lock:
            self._device_name = (name or "").strip()
        self._reload.set()

    def _wanted_device(self):
        with self._lock:
            return self._device_name

    def run(self):
        # 外层循环：每次（重新）打开所选设备；切换设备时回到这里重开。
        while not self.stop.is_set():
            self._reload.clear()
            name = self._wanted_device()
            try:
                mic = pick_loopback_mic_by_name(name)
            except Exception as e:
                self.ui_queue.put(("error", f"无法打开声音设备：{e}"))
                if not self._sleep_until_reload(1.5):
                    return
                continue
            try:
                self._capture(mic)
            except Exception as e:
                self.ui_queue.put(("error", f"录音出错：{e}"))
                if not self._sleep_until_reload(1.5):
                    return

    def _sleep_until_reload(self, seconds):
        """等待一段时间或直到收到切换/停止信号；返回 False 表示需要退出线程。"""
        self._reload.wait(timeout=seconds)
        return not self.stop.is_set()

    def _capture(self, mic):
        frame = int(SAMPLE_RATE * FRAME_SECONDS)
        silence_needed = max(1, int(self.cfg["silence_seconds"] / FRAME_SECONDS))
        max_frames = int(self.cfg["max_utterance_seconds"] / FRAME_SECONDS)
        min_samples = int(self.cfg["min_utterance_seconds"] * SAMPLE_RATE)
        threshold = float(self.cfg.get("energy_threshold") or 0.0)

        buffer = []
        silent_count = 0
        speaking = False

        with mic.recorder(samplerate=SAMPLE_RATE) as rec:
            if threshold <= 0:
                cal = []
                for _ in range(10):
                    d = rec.record(numframes=frame)
                    cal.append(self._rms(self._mono(d)))
                noise = float(np.mean(cal)) if cal else 0.0
                threshold = max(noise * 3.0, 0.004)

            while not self.stop.is_set():
                if self._reload.is_set():
                    return  # 用户切换了设备 -> 退出本次采集，外层会重开
                data = rec.record(numframes=frame)
                mono = self._mono(data)
                if mono is None or mono.size == 0:
                    continue

                if not self.listening.is_set():
                    buffer, speaking, silent_count = [], False, 0
                    continue

                if self._rms(mono) >= threshold:
                    buffer.append(mono)
                    silent_count = 0
                    speaking = True
                elif speaking:
                    buffer.append(mono)
                    silent_count += 1
                    if silent_count >= silence_needed:
                        self._flush(buffer, min_samples)
                        buffer, speaking, silent_count = [], False, 0

                if speaking and len(buffer) >= max_frames:
                    self._flush(buffer, min_samples)
                    buffer, speaking, silent_count = [], False, 0

    @staticmethod
    def _mono(data):
        if data is None:
            return None
        if data.ndim > 1:
            return data.mean(axis=1)
        return data

    @staticmethod
    def _rms(mono):
        if mono is None or mono.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(mono.astype(np.float64)))))

    def _flush(self, buffer, min_samples):
        if not buffer:
            return
        audio = np.concatenate(buffer).astype(np.float32)
        if audio.size < min_samples:
            return
        self.seg_queue.put(audio.copy())
        self.ui_queue.put(("status", "识别中…"))


# --------------------------------------------------------------------------- #
# 转写 + 答案生成线程
# --------------------------------------------------------------------------- #
class Brain(threading.Thread):
    def __init__(self, cfg, context, seg_queue, ui_queue, stop_event,
                 manual_queue=None, answer_all_event=None):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.context = condense_context(
            context, int(cfg.get("resume_max_chars", 3000))
        )
        self.seg_queue = seg_queue
        self.manual_queue = manual_queue
        self.answer_all_event = answer_all_event
        self.ui_queue = ui_queue
        self.stop = stop_event
        self.system_prompt = build_system_prompt(cfg.get("target_role", ""))
        self.researcher = WebSearch(cfg)
        self.target_role = (cfg.get("target_role") or "").strip()
        self.engine = None

    def set_target_role(self, role):
        """动态设置目标岗位：实时重建系统提示词、并让联网检索聚焦该岗位。"""
        role = (role or "").strip()
        self.target_role = role
        self.system_prompt = build_system_prompt(role)
        if self.researcher:
            self.researcher.role = role

    @staticmethod
    def _drain(q):
        """清空队列里积压的内容（丢弃尚未处理的音频段）。"""
        try:
            while True:
                q.get_nowait()
        except queue.Empty:
            pass

    @staticmethod
    def _latest(item, q):
        """只保留最新的一段，丢弃队列里更早的积压段，避免越落越远。"""
        try:
            while True:
                item = q.get_nowait()
        except queue.Empty:
            pass
        return item

    def run(self):
        try:
            self.engine = make_engine(
                self.cfg, lambda s: self.ui_queue.put(("status", s))
            )
        except Exception as e:
            self.ui_queue.put(("error", f"模型加载失败：{e}"))
            return

        # 预热：先把模型载入内存并缓存系统提示词（含简历），
        # 把这一次性的冷启动开销挪到启动时，面试中第一个问题就不会久等。
        # 注意：必须用干净的一次性请求（max_tokens=1, 非流式），
        # 绝不能流式取一个 token 就 break——那会把未读完的生成留在 Ollama
        # 运行槽里，堵塞后面真正的问题（表现为“识别了却迟迟不回答”）。
        self.ui_queue.put(("status", "首次预热模型中（约 1 分钟，仅此一次）…"))
        try:
            personal_msg = None
            if self.context:
                # 用“自我介绍”这类个人题的形态预热，把简历前缀也缓存好
                personal_msg = build_user_message(
                    "请做个自我介绍", self.context, "", True
                )
            self.engine.warmup(self.system_prompt, personal_msg)
        except Exception as e:
            self.ui_queue.put(("status", f"预热未完成（{e}），仍可使用"))
        self.ui_queue.put(("status", "聆听中…（模型已就绪）"))

        while not self.stop.is_set():
            # 整圈处理都包在异常保护里：任何一步出错都降级继续，
            # 绝不让 Brain 线程因未捕获异常而死掉（死掉会导致界面永远停在最后一帧）。
            try:
                # 手动提问拥有最高优先级：先处理它，并丢弃积压的音频段，
                # 避免手动答案被排在后面或被聆听到的噪声覆盖。
                if self.manual_queue is not None:
                    try:
                        mq = self.manual_queue.get_nowait()
                    except queue.Empty:
                        mq = None
                    if mq:
                        self._drain(self.seg_queue)
                        self._handle_question(mq, is_manual=True)
                        self._drain(self.seg_queue)  # 清掉回答期间累积的音频
                        continue

                try:
                    item = self.seg_queue.get(timeout=0.3)
                except queue.Empty:
                    continue
                if item is None:
                    continue

                # 回答模式：
                #  · 只答最新(默认)：丢弃积压旧语音，始终回答最近的问题、不掉队。
                #  · 每题都答：保留全部，按顺序逐题作答（慢但不漏题）。
                answer_all = (self.answer_all_event is not None
                              and self.answer_all_event.is_set())
                if not answer_all:
                    item = self._latest(item, self.seg_queue)

                question = self._transcribe(item)
                if not question or not question.strip():
                    self.ui_queue.put(("status", "（没听清，继续聆听…）"))
                    continue
                self._handle_question(question, is_manual=False)
            except Exception as e:
                # 出现任何意外都报到界面、并继续聆听，绝不崩线程
                try:
                    self.ui_queue.put(("error", f"处理出错（已自动恢复）：{e}"))
                    self.ui_queue.put(("status", "聆听中…"))
                except Exception:
                    pass

    def _manual_pending(self):
        return (self.manual_queue is not None
                and not self.manual_queue.empty())

    def _handle_question(self, question, is_manual=False):
        question = question.strip()
        if not question:
            return
        self.ui_queue.put(("question", question))
        self._answer(question, is_manual=is_manual)

    def _transcribe(self, audio):
        try:
            return self.engine.transcribe(audio)
        except Exception as e:
            self.ui_queue.put(("error", f"语音识别失败：{e}"))
            return ""

    def _answer(self, question, is_manual=False):
        self.ui_queue.put(("answer_start", None))
        # 可选：先联网检索相关案例/资料（失败或超时则跳过，不影响作答）
        snippets = ""
        if self.researcher and self.researcher.enabled:
            self.ui_queue.put(("status", "联网检索相关案例…"))
            try:
                snippets = self.researcher.search(question)
            except Exception:
                snippets = ""  # 检索任何异常都忽略，继续纯本地作答
        # 语音回答：若此时已有手动提问在等，立刻让位，先去回答手动提问
        if not is_manual and self._manual_pending():
            return
        # 判断是不是“问你个人经历/背景”的题：先用模型快速分类(更准)，
        # 失败则回退到关键词；只有个人题才把简历喂进去，避免专业题被简历带偏。
        cls = None
        if self.context:
            cls = self.engine.classify_personal(question)
        if cls is None:
            personal = is_personal_question(question)
        else:
            personal = bool(cls)
        user_q = build_user_message(question, self.context, snippets, personal)
        if snippets:
            self.ui_queue.put(("status", "AI 正在作答（已结合联网资料）…"))
        else:
            self.ui_queue.put(("status", "AI 正在作答…"))
        try:
            gen = self.engine.answer_stream(self.system_prompt, user_q, self.stop)
            for delta in gen:
                self.ui_queue.put(("answer_delta", delta))
                # 语音回答可被手动提问随时打断（手动提问优先级最高）
                if not is_manual and self._manual_pending():
                    try:
                        gen.close()
                    except Exception:
                        pass
                    self.ui_queue.put(("status", "已切到你的手动提问…"))
                    return
            self.ui_queue.put(("answer_done", None))
            self.ui_queue.put(("status", "聆听中…"))
        except Exception as e:
            self.ui_queue.put(("error", f"生成答案失败：{e}"))


# --------------------------------------------------------------------------- #
# 界面
# --------------------------------------------------------------------------- #
class App:
    def __init__(self, root, cfg, context):
        self.root = root
        self.cfg = cfg
        self.ui_queue = queue.Queue()
        self.seg_queue = queue.Queue()
        self.manual_queue = queue.Queue()
        self.listening = threading.Event()
        self.listening.set()
        self.answer_all = threading.Event()
        if str(cfg.get("answer_mode", "latest")).lower() == "all":
            self.answer_all.set()
        self.stop = threading.Event()

        self._build_ui()

        if sc is None:
            self.set_status("启动检查未通过")
            self.append_answer("⚠ 无法启动：缺少 soundcard 库，请运行 setup.bat 安装依赖。\n")
            return

        self.brain = Brain(
            cfg, context, self.seg_queue, self.ui_queue, self.stop,
            manual_queue=self.manual_queue, answer_all_event=self.answer_all,
        )
        self.listener = AudioListener(
            cfg, self.seg_queue, self.ui_queue, self.listening, self.stop
        )
        self.brain.start()
        self.listener.start()
        self.refresh_devices()
        self.root.after(50, self.poll)

    # ----- UI 构建 ----- #
    def _build_ui(self):
        r = self.root
        r.title("AI 面试助手")
        r.geometry("540x680")
        r.minsize(380, 420)
        r.configure(bg="#1e1e1e")
        try:
            r.attributes("-topmost", True)
            r.attributes("-alpha", 0.95)
        except Exception:
            pass

        ui_font = font.Font(family="Microsoft YaHei UI", size=11)
        q_font = font.Font(family="Microsoft YaHei UI", size=12, weight="bold")
        small = font.Font(family="Microsoft YaHei UI", size=9)

        top = tk.Frame(r, bg="#2d2d30")
        top.pack(fill="x")
        self.status_var = tk.StringVar(value="初始化中…")
        tk.Label(
            top, textvariable=self.status_var, bg="#2d2d30", fg="#9cdcfe",
            font=small, anchor="w", padx=8, pady=4
        ).pack(side="left", fill="x", expand=True)

        self.pause_btn = tk.Button(
            top, text="⏸ 暂停聆听", command=self.toggle_listen,
            bg="#3c3c3c", fg="#ffffff", relief="flat", font=small, padx=8
        )
        self.pause_btn.pack(side="right", padx=4, pady=3)
        self.mode_btn = tk.Button(
            top,
            text=("模式:每题都答" if self.answer_all.is_set() else "模式:只答最新"),
            command=self.toggle_answer_mode,
            bg="#3c3c3c", fg="#ffffff", relief="flat", font=small, padx=8
        )
        self.mode_btn.pack(side="right", padx=4, pady=3)
        tk.Button(
            top, text="🗑 清空", command=self.clear, bg="#3c3c3c", fg="#ffffff",
            relief="flat", font=small, padx=8
        ).pack(side="right", padx=4, pady=3)
        self.top_btn = tk.Button(
            top, text="📌 置顶:开", command=self.toggle_topmost,
            bg="#3c3c3c", fg="#ffffff", relief="flat", font=small, padx=8
        )
        self.top_btn.pack(side="right", padx=4, pady=3)

        # 声音来源选择行：随时切换抓取“耳机/扬声器/默认设备”
        devrow = tk.Frame(r, bg="#1e1e1e")
        devrow.pack(fill="x", padx=8, pady=(6, 0))
        tk.Label(devrow, text="🎧 声音来源", bg="#1e1e1e", fg="#9cdcfe",
                 font=small).pack(side="left", padx=(0, 6))
        self.device_var = tk.StringVar()
        self.device_combo = ttk.Combobox(
            devrow, textvariable=self.device_var, state="readonly",
            font=small, height=12
        )
        self.device_combo.pack(side="left", fill="x", expand=True)
        self.device_combo.bind("<<ComboboxSelected>>", self.on_device_change)
        tk.Button(
            devrow, text="🔄", command=self.refresh_devices, bg="#3c3c3c",
            fg="#ffffff", relief="flat", font=small, padx=8
        ).pack(side="left", padx=(6, 0))
        self._web_on = bool(self.cfg.get("web_search", False))
        self.web_btn = tk.Button(
            devrow,
            text=("🌐 联网:开" if self._web_on else "🌐 联网:关"),
            command=self.toggle_web, bg="#3c3c3c", fg="#ffffff",
            relief="flat", font=small, padx=8
        )
        self.web_btn.pack(side="left", padx=(6, 0))
        self._device_options = []  # [(label, name), ...]

        # 目标岗位输入行：直接填你要面的岗位，回车或点应用即生效
        rolerow = tk.Frame(r, bg="#1e1e1e")
        rolerow.pack(fill="x", padx=8, pady=(6, 0))
        tk.Label(rolerow, text="🎯 目标岗位", bg="#1e1e1e", fg="#9cdcfe",
                 font=small).pack(side="left", padx=(0, 6))
        self.role_entry = tk.Entry(rolerow, bg="#3c3c3c", fg="#ffffff",
                                   font=small, relief="flat",
                                   insertbackground="#fff")
        self.role_entry.pack(side="left", fill="x", expand=True, ipady=2)
        self.role_entry.insert(0, self.cfg.get("target_role", "") or "")
        self.role_entry.bind("<Return>", lambda e: self.apply_role())
        tk.Button(
            rolerow, text="应用", command=self.apply_role, bg="#0e639c",
            fg="#ffffff", relief="flat", font=small, padx=10
        ).pack(side="left", padx=(6, 0))
        tk.Label(rolerow, text="（留空=跟着问题走）", bg="#1e1e1e",
                 fg="#666666", font=small).pack(side="left", padx=(6, 0))

        tk.Label(r, text="面试官问题", bg="#1e1e1e", fg="#808080",
                 font=small, anchor="w", padx=8).pack(fill="x", pady=(6, 0))
        self.q_text = scrolledtext.ScrolledText(
            r, height=4, wrap="word", bg="#252526", fg="#ffd479",
            font=q_font, relief="flat", padx=8, pady=6, insertbackground="#fff"
        )
        self.q_text.pack(fill="x", padx=8, pady=(2, 6))

        tk.Label(r, text="建议回答（可直接照着说）", bg="#1e1e1e", fg="#808080",
                 font=small, anchor="w", padx=8).pack(fill="x")
        self.a_text = scrolledtext.ScrolledText(
            r, wrap="word", bg="#252526", fg="#d4ffd4", font=ui_font,
            relief="flat", padx=8, pady=6, insertbackground="#fff"
        )
        self.a_text.pack(fill="both", expand=True, padx=8, pady=(2, 6))

        bottom = tk.Frame(r, bg="#1e1e1e")
        bottom.pack(fill="x", padx=8, pady=(0, 8))
        self.entry = tk.Entry(bottom, bg="#3c3c3c", fg="#ffffff",
                              font=ui_font, relief="flat", insertbackground="#fff")
        self.entry.pack(side="left", fill="x", expand=True, ipady=4)
        self.entry.bind("<Return>", lambda e: self.manual_ask())
        tk.Button(
            bottom, text="手动提问 ▶", command=self.manual_ask,
            bg="#0e639c", fg="#ffffff", relief="flat", font=small, padx=10
        ).pack(side="right", padx=(6, 0))

        r.protocol("WM_DELETE_WINDOW", self.on_close)
        r.bind("<Control-space>", lambda e: self.toggle_listen())

    # ----- 操作 ----- #
    def refresh_devices(self):
        """重新扫描输出设备并填充下拉框（中途插拔耳机后点🔄即可）。"""
        if sc is None:
            return
        self._device_options = list_output_devices()
        labels = [lbl for (lbl, _name) in self._device_options]
        self.device_combo["values"] = labels
        # 让下拉框选中当前监听设备
        cur_name = self.listener._wanted_device() if hasattr(self, "listener") else ""
        idx = 0
        for i, (_lbl, name) in enumerate(self._device_options):
            if name == cur_name:
                idx = i
                break
        if labels:
            self.device_combo.current(idx)

    def on_device_change(self, _event=None):
        """用户在下拉框里选了新的声音来源 -> 立即切换抓取设备。"""
        i = self.device_combo.current()
        if i < 0 or i >= len(self._device_options):
            return
        label, name = self._device_options[i]
        if hasattr(self, "listener"):
            self.listener.set_device(name)
        self.set_status(f"已切换声音来源：{label}")

    def apply_role(self):
        """应用目标岗位：实时让作答和检索聚焦该岗位；并写回 config 以便下次记住。"""
        role = self.role_entry.get().strip()
        if hasattr(self, "brain"):
            self.brain.set_target_role(role)
        self.cfg["target_role"] = role
        save_config_value("target_role", role)
        if role:
            self.set_status(f"已设定目标岗位：{role}（作答与检索将聚焦该岗位）")
        else:
            self.set_status("目标岗位已清空（回答跟着问题本身走）")

    def toggle_listen(self):
        if self.listening.is_set():
            self.listening.clear()
            self.pause_btn.config(text="▶ 继续聆听")
            self.set_status("已暂停聆听")
        else:
            self.listening.set()
            self.pause_btn.config(text="⏸ 暂停聆听")
            self.set_status("聆听中…")

    def toggle_web(self):
        """开关联网检索（开=每题先搜案例再作答，更丰富但慢约几秒；关=纯本地更快）。"""
        self._web_on = not self._web_on
        if hasattr(self, "brain") and getattr(self.brain, "researcher", None):
            self.brain.researcher.enabled = self._web_on
        self.web_btn.config(text=("🌐 联网:开" if self._web_on else "🌐 联网:关"))
        self.set_status("联网检索已" + ("开启（答案更丰富，略慢）" if self._web_on
                                      else "关闭（更快，纯本地）"))

    def toggle_answer_mode(self):
        """切换回答模式：只答最新(跳过积压) ⟷ 每题都答(逐题不漏)。"""
        if self.answer_all.is_set():
            self.answer_all.clear()
            self.mode_btn.config(text="模式:只答最新")
            self.set_status("已切换：只答最新（问得急时跳过旧问题）")
        else:
            self.answer_all.set()
            self.mode_btn.config(text="模式:每题都答")
            self.set_status("已切换：每题都答（按顺序逐题作答，可能稍慢）")

    def toggle_topmost(self):
        cur = bool(self.root.attributes("-topmost"))
        self.root.attributes("-topmost", not cur)
        self.top_btn.config(text=f"📌 置顶:{'关' if cur else '开'}")

    def clear(self):
        self.q_text.delete("1.0", "end")
        self.a_text.delete("1.0", "end")

    def manual_ask(self):
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        if hasattr(self, "brain"):
            self.manual_queue.put(text)
            self.set_status("已提交手动提问，正在作答…")

    # ----- UI 队列轮询 ----- #
    def poll(self):
        # 无论处理单条消息是否出错，都必须重新调度 poll，
        # 否则界面会永久停止刷新（卡住）。
        try:
            while True:
                try:
                    kind, payload = self.ui_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._handle(kind, payload)
                except Exception:
                    traceback.print_exc()
            # 看门狗：万一后台大脑线程意外退出，明确提示而不是默默卡住
            if (hasattr(self, "brain") and self.brain is not None
                    and not self.brain.is_alive()
                    and not getattr(self, "_brain_dead_notified", False)
                    and not self.stop.is_set()):
                self._brain_dead_notified = True
                self.set_status("后台线程已退出，请重启程序（双击 run.bat）")
        finally:
            if not self.stop.is_set():
                self.root.after(50, self.poll)

    def _handle(self, kind, payload):
        if kind == "status":
            self.set_status(payload)
        elif kind == "question":
            self.q_text.delete("1.0", "end")
            self.q_text.insert("end", payload)
            self.a_text.delete("1.0", "end")
        elif kind == "answer_start":
            # 先放一个“思考中”占位，首字到达前也有明确反馈，避免误以为没回答
            self.a_text.delete("1.0", "end")
            self.a_text.insert("end", "（正在思考，请稍候…）")
            self._await_first_delta = True
        elif kind == "answer_delta":
            if getattr(self, "_await_first_delta", False):
                self.a_text.delete("1.0", "end")  # 清掉占位
                self._await_first_delta = False
            self.a_text.insert("end", payload)
            self.a_text.see("end")
        elif kind == "answer_done":
            if getattr(self, "_await_first_delta", False):
                # 整个回答为空时，清掉占位并提示
                self.a_text.delete("1.0", "end")
                self.a_text.insert("end", "（没有生成内容，请重试或改用手动提问）")
                self._await_first_delta = False
            else:
                self.a_text.insert("end", "\n")
        elif kind == "error":
            self.set_status("出错了")
            self.append_answer(f"\n⚠ {payload}\n")

    def set_status(self, text):
        self.status_var.set(text)

    def append_answer(self, text):
        self.a_text.insert("end", text)
        self.a_text.see("end")

    def on_close(self):
        self.stop.set()
        # 释放本地大模型占用的内存（仅本地 Ollama 后端有 closer）
        try:
            eng = getattr(self, "brain", None) and self.brain.engine
            if eng:
                eng.close()
        except Exception:
            pass
        self.root.after(100, self.root.destroy)


def main():
    cfg = load_config()
    context = load_context()
    root = tk.Tk()
    App(root, cfg, context)
    root.mainloop()


if __name__ == "__main__":
    main()
