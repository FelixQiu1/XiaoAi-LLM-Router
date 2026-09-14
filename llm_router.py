"""
【模块 2】llm_router.py —— 思考胶水（LiteLLM 统一出口）
==========================================================
职责：把"system + 历史 + 当前问题"发给配置选定的大模型，拿回纯文本回复。

为什么用 LiteLLM 而不是裸 requests？
  - 一行切换供应商：deepseek / ollama / openai / claude... 全是同一个函数
  - 本地 Ollama 与云端 DeepSeek 走同一条调用路径，代码零分叉

二次开发：
  - 加新 provider：只在 config.yaml 的 llm.<name> 下加一段，
    LiteLLM 的 model 字符串规则 "<provider>/<model>" 自动适配。
  - 想加流式（边想边说）：把 completion 换成 completion(stream=True)，
    在 main.py 里逐 token 喂给 TTS。
"""

import logging

from litellm import acompletion          # 异步版，和 main 的 asyncio 循环同线程
from litellm.exceptions import BadRequestError, AuthenticationError, APIError

log = logging.getLogger("xiaoai-router.llm")

# 各 provider 的默认 system prompt 差异不大，统一在 main.py 里给；
# 这里只负责"把配置翻译成 LiteLLM 的调用参数"。
LITELLM_MODEL_FORMAT = "{provider}/{model}"   # LiteLLM 的路由字符串


def _litellm_target(cfg: dict) -> tuple[str, dict]:
    """把 config.yaml 的 llm 段翻译成 (model_str, extra_params)。

    返回例：
      ("deepseek/deepseek-chat", {})
      ("ollama/qwen2.5:7b", {"api_base": "http://host.docker.internal:11434"})
      ("openai/gpt-4o-mini", {"api_key": "***"})
    """
    llm_cfg = cfg.get("llm", {})
    provider = llm_cfg.get("default_provider", "deepseek")
    p = llm_cfg.get(provider, {}) or {}
    model = p.get("model", "")
    if not model:
        raise ValueError(f"llm.{provider}.model 未配置")

    extra: dict = {}
    if provider == "ollama":
        # 容器内访问宿主机 Ollama：host.docker.internal（compose 已配 extra_hosts）
        extra["api_base"] = p.get("base_url", "http://localhost:11434")
    elif p.get("api_key"):
        extra["api_key"] = p["api_key"]
        # 部分供应商需要自定义 api_base（Azure / 私有网关）
        if p.get("base_url"):
            extra["api_base"] = p["base_url"]

    return LITELLM_MODEL_FORMAT.format(provider=provider, model=model), extra


async def ask(cfg: dict, system_prompt: str, messages: list[dict], user_text: str) -> str:
    """发一次 LLM 请求，返回纯文本回复。

    messages: 由 memory.MemorySession.to_llm_messages() 产出的历史
              [{"role": "user"/"assistant", "content": "..."}, ...]
    调用约定（契约）：
      - 本函数不碰 config 以外的状态 → 方便单测
      - 失败抛 RuntimeError（main.py 统一兜底成"网络开小差"）
    """
    model_str, extra = _litellm_target(cfg)

    # 拼装最终发给模型的对话：system 在最前，历史居中，当前问题压尾
    payload: list[dict] = [{"role": "system", "content": system_prompt}, *messages,
                           {"role": "user", "content": user_text}]

    try:
        resp = await acompletion(model=model_str, messages=payload, **extra)
        # LiteLLM 统一返回 OpenAI 风格结构：choices[0].message.content
        text = (resp.choices[0].message.content or "").strip()
        return text or "（模型没有返回内容）"

    except AuthenticationError as e:
        # 密钥错 → 日志里直接说人话，方便在 docker logs 里一眼定位
        raise RuntimeError(f"{model_str} 鉴权失败：{e}") from e
    except BadRequestError as e:
        raise RuntimeError(f"{model_str} 请求被拒（可能超上下文窗口）：{e}") from e
    except APIError as e:
        raise RuntimeError(f"{model_str} 上游错误：{e}") from e
