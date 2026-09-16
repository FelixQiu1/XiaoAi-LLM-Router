#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XiaoAi-LLM-Router —— 小爱同学全能大模型网关（胶水层入口）
============================================================

设计理念：不造轮子，只做"生态整合"。

  小爱收音 ──(MiService MQTT/poll)──▶ 本程序 ──(LiteLLM)──▶ DeepSeek / Ollama / OpenAI
                                               │
  小爱播放 ◀──(小米 TTS)─────────────────────┘

本文件只做五件事（全部是"胶水"）：
  1. 加载 config.yaml（密钥可用环境变量覆盖）
  2. 建一个共享 aiohttp.ClientSession，注入收音模块与 TTS 模块（v1.1：不再各自新建）
  3. 起 asyncio 事件循环，持续监听小爱对话流（MQTT 优先，自动回落轮询）
  4. 命中唤醒词（"问问 AI"/"深思"）→ 调 llm_router 拿回复 → mi_tts 让小爱念出来
     没命中 → 原样放过去，小爱按老规矩回答（不干扰日常指令）
  5. 优雅退出：SIGTERM 时先排空 TTS 播放队列再停，docker stop 不杀脏

v1.1 变更（对照 v1.0）：
  - 收音源改 MiService MQTT Bridge（可配回落轮询）——旧版"轮询不存在的对话日志"是错的
  - 唤醒词默认值从 "请问" 改为 "问问 AI"（"请问" 出现在 99% 的小爱指令里，会抢答）
  - 共享 HTTP session / 水位线去重 / 流式边想边说 / LLM 失败不污染记忆

二次开发路线（模块都在同一目录，按需替换）：
  - miservice_glue.py : 换收音源（WebSocket / 本地麦克风 ASR）
  - llm_router.py     : 换模型供应商（provider 配置驱动，加 provider 不改代码）
  - memory.py         : 换记忆策略（Redis / 向量库 / 按房间隔离）
  - mi_tts.py         : 换 TTS（edge-tts / 直接生成 mp3）

运行：
  python main.py            # 本地跑（需先 pip install -r requirements.txt）
  docker compose up -d      # 容器跑（见 docker-compose.yml / Dockerfile）
  python main.py --demo     # 演示模式：不依赖 MiService，终端输入 → TTS 念回
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timezone

import aiohttp
import yaml

import llm_router as router
import memory as mem
import mi_tts as tts
import miservice_glue as glue
import mi_conversation as conv      # 【模块 1b】机型无关收音 + 设备自检（MiService 库）

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
    p = path if os.path.exists(path) else "config.example.yaml"
    with open(p, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    # 环境变量覆盖敏感字段（Docker 部署时密钥走 env，不落盘）
    mi = cfg.setdefault("mi", {})
    mi["username"] = os.environ.get("MI_USERNAME", mi.get("username"))
    mi["password"] = os.environ.get("MI_PASSWORD", mi.get("password"))

    llm = cfg.setdefault("llm", {})
    ds = llm.setdefault("deepseek", {})
    ds["api_key"] = os.environ.get("DEEPSEEK_API_KEY", ds.get("api_key"))
    oa = llm.setdefault("openai", {})
    oa["api_key"] = os.environ.get("OPENAI_API_KEY", oa.get("api_key"))
    ol = llm.setdefault("ollama", {})
    ol["base_url"] = os.environ.get("OLLAMA_BASE_URL", ol.get("base_url"))

    msvc = cfg.setdefault("miservice", {})
    msvc["base_url"] = os.environ.get("MISEERVICE_URL", msvc.get("base_url", "http://localhost:8080"))
    msvc["mqtt_host"] = os.environ.get("MISEERVICE_MQTT_HOST", msvc.get("mqtt_host", "127.0.0.1"))

    log.info("config loaded: provider=%s, ingest=%s, device=%s",
             llm.get("default_provider"), msvc.get("ingest", "auto"),
             mi.get("device_id"))
    return cfg


# ---------------------------------------------------------------------------
# 2) 唤醒词判定 + 意图解析
#    默认词表在 config.example.yaml（"问问 AI" / "深思"）。
#    ⚠️ 不要加 "请问"：它出现在小爱 99% 的指令里（"请问今天天气"），
#       一命中就抢答，网关会变成复读机。
#    二次开发钩子：wake_words 支持两种形态
#      - "深思"                      纯字符串 → 命中即路由，system 用默认
#      - {word: 深思, prefix: ...}  命中后把 prefix 拼进 system
# ---------------------------------------------------------------------------
def match_wake(word_list: list, text: str) -> str | None:
    """返回命中的唤醒词；没命中返回 None（整句放给小爱原生应答）。"""
    for w in word_list:
        key = w if isinstance(w, str) else (w.get("word") if isinstance(w, dict) else None)
        if key and key in text:
            return key
    return None


def resolve_system_prompt(wake_words: list, hit_word: str | None, default: str) -> str:
    """把"命中哪个唤醒词"翻译成 system prompt。"""
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
    想插新环节（敏感词过滤 / 工具调用）：在 handle() 的 ①~⑤ 之间加一行。
    """

    def __init__(self, cfg: dict, speaker: "tts.Speaker"):
        self.cfg = cfg
        self.speaker = speaker
        self.wake_words = (cfg.get("trigger", {}) or {}).get("wake_words", ["问问 AI"])
        self.stream = bool((cfg.get("llm", {}) or {}).get("stream", False))
        self.default_prompt = (
            "你是小爱同学的 AI 大脑。用户的话会被 TTS 念出来，"
            "所以回答要口语化、每句不超过 50 字、不要 markdown 符号、"
            "不要说『作为 AI』这类开场白。"
        )
        self.sessions = mem.MemoryStore(
            ttl=(cfg.get("session", {}) or {}).get("ttl_seconds", 600),
            max_turns=(cfg.get("session", {}) or {}).get("max_turns", 10),
        )
        self.speaker_mina = None  # v1.2：MiService 库播报器（set_speaker 注入）

    def set_speaker(self, speaker):
        """v1.2：优先用 MiService 库的 MiNAService 播报（机型指令表已内置）。
        没连上 MiService 时回落 mi_tts 的 HTTP 播报。"""
        self.speaker_mina = speaker

    async def handle(self, item: dict, tts_mode: str = "http") -> None:
        """处理一句小爱对话。item 由收音模块产出：
        {"text", "device_id", "session_id"/"request_id", "ts"}
        tts_mode: "mina"(MiService 库) | "http"(mi_tts HTTP)
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

        # --- ② 拼上下文（多轮记忆）---
        session = self.sessions.get(device)
        history = session.to_llm_messages()
        system = resolve_system_prompt(self.wake_words, hit, self.default_prompt)

        # --- ③ 路由到 LLM（provider 由 config 决定，代码零改动切模型） ---
        ok = True
        if self.stream:
            # ③-a 流式边想边说：每攒够一个短句立即 TTS，小爱的"思考感" 15s → 2s
            buffered: list[str] = []
            try:
                async for chunk in router.ask_stream(self.cfg, system, history, text):
                    buffered.append(chunk)
                    if tts_mode == "mina" and self.speaker_mina is not None:
                        await self.speaker_mina.tts(device, chunk)
                    else:
                        await self.speaker.speak(device, chunk)
            except Exception as e:
                log.error("[%s] LLM stream failed: %s", device, e)
                ok = False
            full_answer = "".join(buffered)
        else:
            # ③-b 非流式：等完整回复再切句播
            try:
                full_answer = await router.ask(self.cfg, system, history, text)
            except Exception as e:
                log.error("[%s] LLM call failed: %s", device, e)
                full_answer = ""
                ok = False

        # --- ④ 写回记忆（v1.1：失败不写兜底话，只记 pending 占位，不污染上下文） ---
        if ok and full_answer:
            session.append(user=text, assistant=full_answer)
        else:
            session.append_pending(user=text)

        # --- ⑤ TTS 回播（非流式路径在此切短句；流式路径 ③-a 已边想边说） ---
        if not self.stream:
            if tts_mode == "mina" and self.speaker_mina is not None:
                # 机型无关嘴：MiService 库 text_to_speech（指令表已按 hardware 适配）
                await self.speaker_mina.tts(device, full_answer) if ok and full_answer \
                    else await self.speaker_mina.tts(device, "刚才网络有点开小差，你能再问一遍吗？")
            else:
                if ok and full_answer:
                    chunk_size = (self.cfg.get("tts", {}) or {}).get("max_chunk_chars", 60)
                    await self.speaker.speak_chunks(device, full_answer, chunk_size)
                else:
                    await self.speaker.speak(device, "刚才网络有点开小差，你能再问一遍吗？")

        if ok:
            log.info("[%s] replied via %s: %s", device,
                     (self.cfg.get("llm", {}) or {}).get("default_provider"),
                     full_answer[:60])


# ---------------------------------------------------------------------------
# 4) 演示模式（--demo）：不依赖 MiService，终端输入 → TTS 念回
#    没有小米设备的人也能 1 分钟跑通全链路；也当 CI 冒烟用。
# ---------------------------------------------------------------------------
async def run_demo(cfg: dict) -> None:
    log.info("demo mode: 从 stdin 读问题（'quit' 退出）。TTS 走 MiService 配置，"
             "没配 MiService 时只打印文字。")
    pipeline = RouterPipeline(cfg, speaker=tts.Speaker(cfg, _shared_session))
    while True:
        try:
            line = await asyncio.get_running_loop().run_in_executor(None, input, "你: ")
        except (EOFError, KeyboardInterrupt):
            break
        line = line.strip()
        if line in ("quit", "exit", "q"):
            break
        if not line:
            continue
        await pipeline.handle({"text": line, "device_id": "demo",
                               "session_id": "", "ts": 0.0})
    log.info("demo stopped")


# ---------------------------------------------------------------------------
# 5) 入口：共享 session + 事件循环 + 优雅退出
# ---------------------------------------------------------------------------
_shared_session: aiohttp.ClientSession | None = None


async def _list_devices(cfg: dict, hardware_override: str | None) -> None:
    """--list-devices：列出账号下所有小爱音箱 + 档位。跑完退出。"""
    sp = await conv.MiSpeaker.connect(cfg)
    rows = await sp.list_devices()
    if hardware_override:
        rows = [r for r in rows if r["hardware"].upper() == hardware_override.upper()]
    if not rows:
        print("账号下没有发现小爱音箱（检查 MI_USER/MI_PASS 或设备是否登录米家）")
        return
    print(f"{'名称':<20} {'DID':<14} {'hardware':<8} {'档位':<10} 备注")
    for r in rows:
        tier_label = {"perfect": "✅ 完美", "normal": "🚗 正常",
                     "unknown": "❓ 未知", "unsupported": "❌ 不支持"}.get(r["tier"], r["tier"])
        print(f"{str(r['name']):<20} {r['did']:<14} {r['hardware']:<8} {tier_label:<10} {r.get('note','')}")


async def _consume_conversation(sp: "conv.MiSpeaker", stop_event) -> AsyncIterator[dict]:
    """conversation 收音：轮询 get_latest_ask，以 request_id 做水位线去重（重启不重放）。"""
    import collections
    dedup = collections.OrderedDict()   # request_id → ts，有界 LRU
    CAP = 512
    last_ts = 0.0
    log.info("conversation ingest: polling get_latest_ask every 3s")
    while not stop_event.is_set():
        try:
            item = await sp.latest_ask()
        except Exception as e:
            log.warning("conversation poll failed: %s", e)
            await asyncio.sleep(3.0)
            continue
        if item and item.get("request_id"):
            rid = item["request_id"]
            if item["ts"] <= last_ts or rid in dedup:
                await asyncio.sleep(3.0)
                continue
            dedup[rid] = item["ts"]
            dedup.move_to_end(rid)
            while len(dedup) > CAP:
                dedup.popitem(last=False)
            last_ts = item["ts"]
            yield item
        await asyncio.sleep(3.0)


async def main() -> None:
    global _shared_session
    cfg = load_config()

    parser = argparse.ArgumentParser(description="XiaoAi-LLM-Router")
    parser.add_argument("--demo", action="store_true",
                        help="演示模式：stdin 输入 → TTS，不依赖 MiService")
    parser.add_argument("--list-devices", action="store_true",
                        help="列出当前小米账号下所有小爱音箱 + 支持档位后退出")
    parser.add_argument("--hardware", default=None,
                        help="手动指定音箱 hardware（覆盖 device_list 自动识别）")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    args, _ = parser.parse_known_args()
    if args.list_devices:
        await _list_devices(cfg, args.hardware)
        return
    if args.config != "config.yaml":
        cfg = load_config(args.config)

    # 共享 HTTP session（v1.1 核心修正：模块 1 收音 + 模块 4 TTS 复用同一个）
    _shared_session = aiohttp.ClientSession()
    speaker = tts.Speaker(cfg, _shared_session)

    if args.demo:
        try:
            await run_demo(cfg)
        finally:
            await _shared_session.close()
        return

    pipeline = RouterPipeline(cfg, speaker)
    if speaker is not None:
        pipeline.set_speaker(speaker)
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _on_signal(*_):
        log.info("signal received, draining...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _on_signal)

    log.info("XiaoAi-LLM-Router started. 说『小爱同学，%s …』触发。",
             self_first_word(cfg.get("trigger", {})))

    # 周期性清理僵尸 session（每 60s；运维钩子）
    async def _purge_loop():
        while not stop_event.is_set():
            await asyncio.sleep(60)
            pipeline.sessions.purge_expired()

    purge_task = asyncio.create_task(_purge_loop())

    # ---- 收音源分发（v1.2：conversation 优先，poll 兜底）----
    ingest = (cfg.get("miservice", {}) or {}).get("ingest", "auto")
    speaker = None
    if ingest in ("auto", "conversation"):
        try:
            speaker = await conv.MiSpeaker.connect(cfg)
            log.info("conversation ingest ready (MiService 库)")
        except Exception as e:
            log.warning("conversation ingest unavailable (%s)%s", e,
                        "，回落 poll" if ingest == "auto" else "")
            if ingest == "conversation":
                raise
    tts_mode = "mina" if speaker else "http"

    try:
        if speaker is not None:
            async for item in _consume_conversation(speaker, stop_event):
                await pipeline.handle(item, tts_mode)
                if stop_event.is_set():
                    break
        else:
            async for item in glue.consume(cfg, _shared_session):
                await pipeline.handle(item, tts_mode)
                if stop_event.is_set():
                    break
    finally:
        purge_task.cancel()
        await _shared_session.close()

    log.info("router stopped cleanly at %s",
             datetime.now(timezone.utc).isoformat())


def self_first_word(trigger: dict) -> str:
    ww = trigger.get("wake_words") or ["问问 AI"]
    first = ww[0]
    return first if isinstance(first, str) else str(first.get("word", "问问 AI"))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass