"""
【模块 4】mi_tts.py —— 嘴（小米 TTS 回放）
===========================================
职责：把 LLM 的回复送进小爱音箱播放。

当前实现：调用 MiService 的 TTS 接口（aiohttp POST）。

v1.1 修正点：
  - speak() 不再每句新建 aiohttp.ClientSession（旧版每句一次 TCP 握手，
    长回复 5 句就是 5 次握手，白白加延迟）。
    现在 session 由 main.py 创建一次、注入进来（胶水层共享资源，模块保持纯函数）。

二次开发钩子：
  - 换 edge-tts：speak() 里改成 edge_tts.Communicate(text, voice).save(mp3)，
    再走 MiService 的"播放音频"接口。
  - "边想边说"：main.py 消费 router.ask_stream()，每攒够一个句子就 speak() 一次。
    （v1.1 已支持：config llm.stream=true 即开）

注意：speak() 返回时播放"已提交"，不是"已播完"。
      想做语音打断（barge-in）就得在这里接 TTS 完成回调。
"""

from __future__ import annotations

import logging
import re

import aiohttp

log = logging.getLogger("xiaoai-router.tts")

# MiService 的 TTS 接口路径（不同版本可能不同，在 config miservice.tts_path 里改）
DEFAULT_TTS_PATH = "/api/v1/tts"


def split_sentences(text: str, max_chars: int = 60) -> list[str]:
    """把长回复切成"短句"，逐句播 → 模拟真人换气停顿。

    规则（极简）：
      1. 先按句号 / 问号 / 感叹号 / 换行切
      2. 切完的单句仍 > max_chars → 再按逗号切
      3. 空串丢弃
    二次开发：接个分句库（jieba + 标点规则）效果更好。
    """
    text = (text or "").strip()
    if not text:
        return []

    out: list[str] = []
    for p in re.split(r"(?<=[。？！\n])", text):
        p = p.strip()
        if not p:
            continue
        if len(p) <= max_chars:
            out.append(p)
            continue
        sub = re.findall(r".{1,%d}(?=[，、；,])|.+?" % max_chars, p)
        out.extend(s.strip() for s in sub if s and s.strip())
    return out


class Speaker:
    """TTS 播放器：持有一个共享 aiohttp.ClientSession（由 main.py 注入）。

    用法：
        sp = Speaker(cfg, http_session)
        await sp.speak(device_id, "你好")
    """

    def __init__(self, cfg: dict, session: aiohttp.ClientSession):
        self.cfg = cfg
        self.session = session

    def _url(self) -> str:
        m = self.cfg.get("miservice", {}) or {}
        base = (m.get("base_url", "http://localhost:8080")).rstrip("/")
        return f"{base}{m.get('tts_path', DEFAULT_TTS_PATH)}"

    async def speak(self, device_id: str, text: str) -> None:
        """让指定设备念出 text。失败只打日志（不抛），
        因为"念不出来"不该中断整个对话流程。"""
        text = (text or "").strip()
        if not text:
            return
        payload = {"device": device_id, "text": text}
        try:
            async with self.session.post(
                self._url(), json=payload,
                timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status >= 400:
                    log.error("TTS failed: HTTP %s %s", resp.status, await resp.text())
                else:
                    log.info("tts → %s: %s", device_id,
                             text[:30] + ("…" if len(text) > 30 else ""))
        except aiohttp.ClientError as e:
            log.error("TTS unreachable: %s", e)

    async def speak_chunks(self, device_id: str, answer: str, max_chars: int = 60) -> None:
        """把完整回复切成短句逐句播（main.py 非流式路径走这里）。"""
        for chunk in split_sentences(answer, max_chars):
            await self.speak(device_id, chunk)
