"""
【模块 2】llm_router.py —— 思考胶水（LiteLLM 统一出口）
==========================================================
职责：把"system + 历史 + 当前问题"发给配置选定的大模型，拿回纯文本回复。

为什么用 LiteLLM 而不是裸 requests？
  - 一行切换供应商：deepseek / ollama / openai / claude... 全是同一个函数
  - 本地 Ollama 与云端 DeepSeek 走同一条调用路径，代码零分叉

v1.1 修正点：
  - acompletion 加显式 timeout（默认 60s；Ollama 冷启动加载大模型可能很久，可配 llm.timeout_seconds）
  - Ollama 走 LiteLLM 时补上 api_key="ollama"（部分版本缺 key 会 401）
  - 新增 ask_stream()：逐句产出，配合 mi_tts 的"边想边说"（config llm.stream=true）

二次开发：
  - 加新 provider：只在 config.yaml 的 llm.<name> 下加一段，
    LiteLLM 的 model 字符串规则 "<provider>/<model>" 自动适配。
"""

from __future__ import annotations

import logging

from litellm import acompletion
from litellm.exceptions import BadRequestError, AuthenticationError, APIError

# 流式：本版本 litellm 没有独立的 astream() 出口 —— 用 acompletion(stream=True)，
# 返回的响应本身是 async 生成器（chunk.choices[0].delta.content）。

log = logging.getLogger("xiaoai-router.llm")

LITELLM_MODEL_FORMAT = "{provider}/{model}"


def _litellm_target(cfg: dict) -> tuple[str, dict]:
    """把 config.yaml 的 llm 段翻译成 (model_str, extra_params)。

    返回例：
      ("deepseek/deepseek-chat", {"api_key": "***", "timeout": 60})
      ("ollama/qwen2.5:7b", {"api_base": "...", "api_key": "ollama", "timeout": 120})
    """
    llm_cfg = cfg.get("llm", {}) or {}
    provider = llm_cfg.get("default_provider", "deepseek")
    p = llm_cfg.get(provider, {}) or {}
    model = p.get("model", "")
    if not model:
        raise ValueError(f"llm.{provider}.model 未配置")

    # 显式超时：Ollama 默认更宽松（冷启动加载模型），可配 llm.timeout_seconds
    timeout = float(llm_cfg.get("timeout_seconds",
                                120 if provider == "ollama" else 60))

    extra: dict = {"timeout": timeout}
    if provider == "ollama":
        extra["api_base"] = p.get("base_url", "http://localhost:11434")
        # LiteLLM 的 ollama 路由要求 api_key 非空（本地随便给个占位）
        extra["api_key"] = p.get("api_key") or "ollama"
    else:
        if p.get("api_key"):
            extra["api_key"] = p["api_key"]
        if p.get("base_url"):
            extra["api_base"] = p["base_url"]

    return LITELLM_MODEL_FORMAT.format(provider=provider, model=model), extra


def _payload(system_prompt: str, messages: list[dict], user_text: str) -> list[dict]:
    """拼装发给模型的对话：system 在最前，历史居中，当前问题压尾。"""
    return ([{"role": "system", "content": system_prompt}, *messages,
             {"role": "user", "content": user_text}])


async def ask(cfg: dict, system_prompt: str, messages: list[dict], user_text: str) -> str:
    """发一次 LLM 请求，返回纯文本回复。

    契约：
      - 本函数不碰 config 以外的状态 → 方便单测
      - 失败抛 RuntimeError（main.py 统一兜底，且失败回复不入记忆）
    """
    model_str, extra = _litellm_target(cfg)
    payload = _payload(system_prompt, messages, user_text)

    try:
        resp = await acompletion(model=model_str, messages=payload, **extra)
        text = (resp.choices[0].message.content or "").strip()
        return text or "（模型没有返回内容）"
    except AuthenticationError as e:
        raise RuntimeError(f"{model_str} 鉴权失败：{e}") from e
    except BadRequestError as e:
        raise RuntimeError(f"{model_str} 请求被拒（可能超上下文窗口）：{e}") from e
    except APIError as e:
        raise RuntimeError(f"{model_str} 上游错误：{e}") from e


def _split_sentence(text: str, min_chars: int = 12) -> str | None:
    """流式攒句：够长且以句末标点收尾才吐出一句（TTS 边想边说的最小单元）。"""
    t = text.strip()
    if not t:
        return None
    if len(t) >= min_chars and t[-1] in "。？！.?!\n":
        return t
    return None


async def ask_stream(cfg: dict, system_prompt: str, messages: list[dict],
                     user_text: str):
    """流式版 ask()：攒够一个短句就 yield，最后一句没有标点也会收尾吐出。

    用法（main.py 的"边想边说"）：
        async for chunk in router.ask_stream(cfg, sys, hist, text):
            await tts.speak(cfg, device, chunk)

    失败仍抛 RuntimeError（调用方兜底）。
    """
    model_str, extra = _litellm_target(cfg)
    payload = _payload(system_prompt, messages, user_text)

    try:
        resp = await acompletion(model=model_str, messages=payload, stream=True, **extra)
        buf = ""
        async for chunk in resp:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if not delta:
                continue
            buf += delta
            # 逐字符扫描句末标点，攒够就吐（简单状态机，够用且零依赖）
            while buf:
                sent_end = -1
                for i, ch in enumerate(buf):
                    if ch in "。？！.?!\n":
                        sent_end = i + 1
                        break
                if sent_end > 0 and sent_end >= 12:
                    out, buf = buf[:sent_end].strip(), buf[sent_end:]
                    if out:
                        yield out
                elif len(buf) >= 120:   # 模型一直不打标点（代码/长句）→ 强行切
                    out, buf = buf[:120], buf[120:]
                    yield out
        if buf.strip():
            yield buf.strip()
    except AuthenticationError as e:
        raise RuntimeError(f"{model_str} 鉴权失败：{e}") from e
    except APIError as e:
        raise RuntimeError(f"{model_str} 流式上游错误：{e}") from e
