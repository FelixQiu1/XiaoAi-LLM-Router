#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XiaoAi-LLM-Router —— 小爱同学全能大模型网关（胶水层入口）
============================================================

设计理念：不造轮子，只做"生态整合"。

  小爱收音 ──(MiService)──▶ 本程序 ──(LiteLLM)──▶ DeepSeek / Ollama / OpenAI
                                          │
  小爱播放 ◀──(小米 TTS)──────────────────┘

本文件只做四件事（全部是"胶水"）：
  1. 加载 config.yaml（密钥可用环境变量覆盖）
  2. 起一个 asyncio 事件循环，持续监听 MiService 推来的小爱对话
  3. 命中唤醒词（"请问"/"深思"）→ 调 llm_router 拿回复 → mi_tts 让小爱念出来
  4. 没命中唤醒词 → 原样放过去，小爱按老规矩回答（不干扰日常指令）

二次开发路线（模块都在同一目录，按需替换）：
  - miservice_glue.py : 换收音源（WebSocket / 其他小爱协议实现）
  - llm_router.py     : 换模型供应商（provider 是配置驱动，加 provider 不改代码）
  - memory.py         : 换记忆策略（Redis / 向量库 / 按房间隔离）
  - mi_tts.py         : 换 TTS（edge-tts / say / 直接生成 mp3）

运行：
  python main.py            # 本地跑
  docker compose up -d      # 容器跑（见 docker-compose.yml）
"""

import asyncio                      # 异步事件循环：监听是"长连接"式任务，必须 async
import logging
import os
import signal
from datetime import datetime, timezone

import yaml                        # 读 config.yaml
import miservice_glue as glue      # 【模块 1】收音：把 MiService 的对话流转成 dict 流
import llm_router as router        # 【模块 2】思考：LiteLLM 统一出口 + provider 切换
import memory as mem               # 【模块 3】记忆：按设备维护多轮 session
import mi_tts as tts               # 【模块 4】嘴：小米 TTS 播放回复

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("xiaoai-router")


# ---------------------------------------------------------------------------
# 1) 配置加载（胶水：YAML + 环境变量双通道，环境变量优先）
#    二次开发：想换成 Consul / etcd？把 load_config 换掉即可，下游不变。
# ---------------------------------------------------------------------------
def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    # 环境变量覆盖敏感字段（Docker 部署时密钥走 env，不落盘）
    # os.environ.get 的第三个参数是"环境变量没设时的默认值"，
    # 这里把默认值设成 YAML 里的值 → 实现"env 优先、YAML 兜底"。
    mi = cfg.setdefault("mi", {})
    mi["username"] = os.environ.get("MI_USERNAME", mi.get("username"))
    mi["password"] = os.environ.get("MI_PASSWORD", mi.get("password"))

    ds = cfg.get("llm", {}).get("deepseek", {})
    ds["api_key"] = os.environ.get("DEEPSEEK_API_KEY", ds.get("api_key"))
    oa = cfg.get("llm", {}).get("openai", {})
    oa["api_key"] = os.environ.get("OPENAI_API_KEY", oa.get("api_key"))
    ol = cfg.get("llm", {}).get("ollama", {})
    ol["base_url"] = os.environ.get("OLLAMA_BASE_URL", ol.get("base_url"))

    log.info("config loaded: provider=%s, device=%s",
             cfg.get("llm", {}).get("default_provider"),
             cfg.get("mi", {}).get("device_id"))
    return cfg


# ---------------------------------------------------------------------------
# 2) 唤醒词判定 + 意图解析（胶水：字符串匹配，O(1)）
#    二次开发钩子：wake_words 支持两种形态
#      - "请问"            纯字符串 → 命中即路由，system prompt 用默认
#      - "深思": {prefix: "你正在深思模式..."}  命中后把 prefix 拼进 system
#    想加"算一下"触发计算器？在这里加一个分支，返回 (True, tool_name)。
# ---------------------------------------------------------------------------
def match_wake(word_list: list, text: str) -> str | None:
    """返回命中的唤醒词；没命中返回 None（整句放给小爱自己答）。"""
    for w in word_list:
        key = w if isinstance(w, str) else w.get("word")
        if key and key in text:
            return key
    return None


def resolve_system_prompt(wake_words: list, hit_word: str | None, default: str) -> str:
    """把"命中哪个唤醒词"翻译成 system prompt。

    默认：你是小爱同学的 AI 大脑，回答口语化、短平快（后面要被 TTS 念出来）。
    "深思"模式：追加推理要求。
    """
    if not hit_word:
        return default
    for w in wake_words:
        if isinstance(w, dict) and w.get("word") == hit_word:
            prefix = w.get("prefix")
            return f"{default}\n{prefix}" if prefix else default
    return default


# ---------------------------------------------------------------------------
# 3) 核心处理循环（胶水：把 4 个模块粘成一条流水线）
# ---------------------------------------------------------------------------
class RouterPipeline:
    """一条 收音 → 唤醒判定 → LLM → 记忆 → TTS 的异步流水线。

    每处理一句小爱对话，就是一次"回合"。
    想插新环节（比如敏感词过滤 / 工具调用）：在 handle() 里加一行。
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.wake_words = cfg.get("trigger", {}).get("wake_words", [])
        self.default_prompt = (
            "你是小爱同学的 AI 大脑。用户的话会被 TTS 念出来，"
            "所以回答要口语化、每句不超过 50 字、不要 markdown 符号。"
        )
        # memory 与 router 都按设备隔离 → 多个小爱音箱可以共用一个网关
        self.sessions = mem.MemoryStore(
            ttl=cfg.get("session", {}).get("ttl_seconds", 600),
            max_turns=cfg.get("session", {}).get("max_turns", 10),
        )

    async def handle(self, item: dict) -> None:
        """处理一句小爱对话。item 由 miservice_glue 产出，形如：
        {"text": "用户说的话", "device_id": "...", "session_id": "..."}
        """
        text = (item.get("text") or "").strip()
        device = item.get("device_id", "default")
        if not text:
            return

        log.info("[%s] heard: %s", device, text)

        # --- ① 唤醒词判定：没命中 → 放行给小爱原生应答，网关隐身 ---
        hit = match_wake(self.wake_words, text)
        if hit is None:
            log.debug("[%s] no wake word, pass through to native Xiaoi", device)
            return

        # --- ② 拼上下文（多轮记忆）：把"它刚才说的那个"补全成 LLM 能懂的对话 ---
        session = self.sessions.get(device)
        history = session.to_llm_messages()          # [{role, content}, ...]
        system = resolve_system_prompt(self.wake_words, hit, self.default_prompt)

        # --- ③ 路由到 LLM（provider 由 config 决定，代码零改动切模型） ---
        try:
            answer = await router.ask(
                self.cfg,
                system_prompt=system,
                messages=history,
                user_text=text,
            )
        except Exception as e:                      # 胶水层的容错：LLM 挂了不能拖死监听
            log.error("[%s] LLM call failed: %s", device, e)
            answer = "刚才网络有点开小差，你能再问一遍吗？"

        # --- ④ 写回记忆（下一句"它刚才说的..."才接得住） ---
        session.append(user=text, assistant=answer)

        # --- ⑤ TTS 回播：切短句逐句念，模拟真人停顿 ---
        chunk_size = self.cfg.get("tts", {}).get("max_chunk_chars", 60)
        for chunk in tts.split_sentences(answer, chunk_size):
            await tts.speak(self.cfg, device, chunk)

        log.info("[%s] replied (via %s): %s",
                 device, self.cfg.get("llm", {}).get("default_provider"), answer)


# ---------------------------------------------------------------------------
# 4) 入口：起事件循环 + 优雅退出（胶水：信号处理，让 docker stop 不杀脏）
# ---------------------------------------------------------------------------
async def main() -> None:
    cfg = load_config()
    pipeline = RouterPipeline(cfg)

    # 监听 MiService 的对话流：一个"永不返回"的 async for 循环。
    # 二次开发：想加 WebSocket 收音，就改 glue.consume() 的实现，main 不动。
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _on_signal(*_):
        log.info("signal received, draining...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _on_signal)

    log.info("XiaoAi-LLM-Router started. Saying '小爱同学，%s …' to trigger.",
             (cfg.get("trigger", {}).get("wake_words") or ["请问"])[0])

    async for item in glue.consume(cfg):
        await pipeline.handle(item)
        if stop_event.is_set():
            break

    log.info("router stopped cleanly at %s", datetime.now(timezone.utc).isoformat())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
