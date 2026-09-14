"""
【模块 4】mi_tts.py —— 嘴（小米 TTS 回放）
===========================================
职责：把 LLM 的回复送进小爱音箱播放。

当前实现：调用 MiService 的 TTS 接口（aiohttp POST）。
二次开发钩子：
  - 换 edge-tts：speak() 里改成 edge_tts.Communicate(text, voice).save(mp3)，
    再走 MiService 的"播放音频"接口。
  - 想要"边想边说"：main.py 改成消费 LLM 流，每攒够一个句子就 speak() 一次。

注意：speak() 返回前播放"已提交"，不是"已播完"。
      想做语音打断（barge-in）就得在这里接 TTS 完成回调。
"""

import logging
import re

import aiohttp

log = logging.getLogger("xiaoai-router.tts")

# MiService 的 TTS 接口路径（以你部署的 MiService 版本控制台为准）
MISEVICE_TTS_PATH = "/api/v1/tts"


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

    # 第一刀：强断句符
    parts = re.split(r"(?<=[。？！\n])", text)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(p) <= max_chars:
            out.append(p)
            continue
        # 第二刀：逗号 / 分号（弱断句符，凑够长度就切）
        sub = re.findall(r".{1,%d}(?=[，、；,])|.+?" % max_chars, p)
        out.extend(s.strip() for s in sub if s and s.strip())
    return out


async def speak(cfg: dict, device_id: str, text: str) -> None:
    """让指定设备念出 text。失败只打日志（不抛），
    因为"念不出来"不该中断整个对话流程。"""
    if not text:
        return
    base = cfg.get("miservice", {}).get("base_url", "http://localhost:8080")
    url = f"{base.rstrip('/')}{MISEVICE_TTS_PATH}"
    payload = {"device": device_id, "text": text}

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status >= 400:
                    log.error("TTS failed: HTTP %s %s", resp.status, await resp.text())
                else:
                    log.info("tts → %s: %s", device_id, text[:30] + ("…" if len(text) > 30 else ""))
        except aiohttp.ClientError as e:
            log.error("TTS unreachable: %s", e)
