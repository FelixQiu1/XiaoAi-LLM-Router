"""
【模块 1】miservice_glue.py —— 收音胶水
==========================================
职责：把 MiService 推来的小爱对话，变成"一个 dict 流"喂给 main.py。

为什么单独成模块？
  收音源迟早要换（WebSocket 推流 / 另一套小爱协议 / 麦克风本地 ASR）。
  把"收音"隔离在这里，main.py 的流水线永远只认
  {"text": str, "device_id": str} 这个契约。

实现方式（当前版）：
  轮询 MiService 网关的对话日志接口（aiohttp 异步 GET）。
  二次开发：把 consume() 换成 websocket 连接，或本地麦克风
  faster-whisper ASR，只要保持产出 dict 即可。
"""

import asyncio
import json
import logging

import aiohttp

log = logging.getLogger("xiaoai-router.glue")

# MiService 对话接口的常见路径（不同版本可能不同，用环境变量 MiService 控制台核对）
MISEVICE_TALKS_PATH = "/api/v1/talks"
POLL_INTERVAL_SECONDS = 2.0   # 轮询间隔：够快（小爱回复延迟可接受）又省 CPU


def _item(payload: dict) -> dict | None:
    """把 MiService 的一条原始记录翻译成我们的契约 dict。

    原始字段（示例）：
      {"id": "abc", "device": "1A2B...", "user": "请问今晚吃什么", "bot": "..."}
    我们只取 user 侧 + 设备号。
    """
    text = (payload.get("user") or payload.get("text") or "").strip()
    device = payload.get("device") or payload.get("device_id") or "default"
    if not text:
        return None
    return {"text": text, "device_id": device, "session_id": payload.get("id", "")}


async def consume(cfg: dict):
    """异步生成器：不断轮询 MiService，产出新的用户对话。

    已见过的记录 id 用集合去重（重启不重播、不丢句）。
    二次开发钩子：
      - 想改成"实时推送"？把 while 换成 websockets 连接即可，
        yield 的结构不变。
      - 想加"只监听某台音箱"？在 cfg['miservice'] 加 device_whitelist，
        在这里过滤。
    """
    base = cfg.get("miservice", {}).get("base_url", "http://localhost:8080")
    url = f"{base.rstrip('/')}{MISEVICE_TALKS_PATH}"
    seen: set[str] = set()

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        log.info("polling MiService at %s every %.1fs", url, POLL_INTERVAL_SECONDS)
        while True:
            try:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        log.warning("MiService returned %s, retrying", resp.status)
                    else:
                        records = await resp.json()
                        # 兼容两种返回形态：[ {...} ] 或 {"items": [ {...} ]}
                        records = records.get("items", records) if isinstance(records, dict) else records
                        for rec in records or []:
                            rid = str(rec.get("id", ""))
                            if rid and rid in seen:
                                continue
                            if rid:
                                seen.add(rid)
                            item = _item(rec)
                            if item:
                                yield item
            except (aiohttp.ClientError, json.JSONDecodeError) as e:
                # 网关临时挂了不能拖死整个监听循环 → 睡一会儿再轮
                log.warning("MiService unreachable (%s), backoff %.1fs", e, POLL_INTERVAL_SECONDS)

            await asyncio.sleep(POLL_INTERVAL_SECONDS)
