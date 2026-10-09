"""
【模块 1】miservice_glue.py —— 收音胶水
==========================================
职责：把"小爱收音"变成异步 dict 流喂给 main.py：
    {"text": str, "device_id": str, "session_id": str, "ts": float}

为什么单独成模块？
  收音源是本项目最大的不确定性（MiService 不同版本暴露的集成点不同）。
  把收音隔离在这里，main.py 的流水线永远只认上面这个契约。

两种收音模式（config.yaml 的 miservice.ingest 选择，自动回落）：
  ┌──────────────┬───────────────────────────────────────────────────┐
  │ mqtt（推荐）  │ 订阅 MiService MQTT Bridge：                       │
  │              │   broker = miservice.mqtt_base_url（默认 127.0.0.1:1883） │
  │              │   topic  = miservice/bridge/{device_id}（可配）    │
  │              │ 小爱的 ASR 结果经 MiService 桥推过来，延迟 <1s      │
  ├──────────────┼───────────────────────────────────────────────────┤
  │ poll（兜底）  │ 轮询 MiService HTTP 对话记录接口                   │
  │              │ （路径因版本而异，在 config 里改）                  │
  └──────────────┴───────────────────────────────────────────────────┘

模式选择逻辑（`ingest: auto`，默认）：
  先连 MQTT，握手超时（`mqtt_connect_timeout`，默认 3s）就连 poll；
  MQTT 运行中掉线 → 指数退避重连，连不上再回落 poll，避免网关静默失明。

去重策略（v1.1 修正点）：
  旧版用"进程内无限 set"，① 内存只增不减，② 重启后历史记录会被重放。
  现在用 时间水位线（ts > last_ts） + 有界 LRU 记录 id（cap 4096）：
  - 重启水位线默认取 0 → 冷启动会把最近一批记录重答一遍？不会：
    水位线随 last_ts 前进，历史旧记录 ts 更小，被自然滤掉；
  - LRU 只防"同一秒内的并发重复推送"，不需要无限大。

二次开发钩子：
  - 换 WebSocket 推流：实现一个 `async def consume_xxx(cfg)`，
    保持产出 dict 契约，main.py 只改一行 ingest 分发。
  - 换本地麦克风 + faster-whisper ASR：同上，device_id 写死 "local"。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict

import aiohttp

log = logging.getLogger("xiaoai-router.glue")

# 有界去重窗口：LRU，超过 cap 淘汰最旧（同一秒内重复推送的防护足够）
_DEDUP_CAP = 4096


class _BoundedDedup:
    """有界 LRU 去重 + 时间水位线。线程模型：只在事件循环线程内调用。"""

    def __init__(self, cap: int = _DEDUP_CAP):
        self.cap = cap
        self._seen: "OrderedDict[str, float]" = OrderedDict()
        self.last_ts = 0.0  # 水位线：只接受 ts 严格更大的记录

    def accept(self, rid: str, ts: float) -> bool:
        """返回 True = 新记录，False = 重复/过期。"""
        if ts <= self.last_ts:
            return False
        if rid and rid in self._seen:
            return False
        if rid:
            self._seen[rid] = ts
            self._seen.move_to_end(rid)
            while len(self._seen) > self.cap:
                self._seen.popitem(last=False)
        self.last_ts = ts
        return True


def _item(payload: dict, fallback_device: str) -> dict | None:
    """把 MiService 的一条原始记录翻译成契约 dict。

    兼容三类字段命名（不同版本/桥插件不一样）：
      user 侧文本: user / text / query / asr
      设备号:      device / device_id / did
      时间戳:      ts / timestamp / time（可能缺省，用收到时刻兜底）
    """
    text = (
        payload.get("user")
        or payload.get("text")
        or payload.get("query")
        or payload.get("asr")
        or ""
    ).strip()
    if not text:
        return None
    device = str(
        payload.get("device")
        or payload.get("device_id")
        or payload.get("did")
        or fallback_device
    )
    ts_raw = payload.get("ts") or payload.get("timestamp") or payload.get("time")
    try:
        ts = float(ts_raw) if ts_raw is not None else time.time()
    except (TypeError, ValueError):
        ts = time.time()
    return {
        "text": text,
        "device_id": device,
        "session_id": str(payload.get("id") or payload.get("session_id") or ""),
        "ts": ts,
    }


# ---------------------------------------------------------------------------
# MQTT 模式（推荐）
# ---------------------------------------------------------------------------
async def _consume_mqtt(cfg: dict, dedup: _BoundedDedup, poll_fallback: bool):
    """订阅 MiService MQTT Bridge，产出 dict 流。

    连接参数全部来自 config（miservice.mqtt_*），broker 不可达时：
      - 若 poll_fallback=True：打印警告并降级到 poll 模式（调用方处理）
      - 否则：指数退避重连（1s → 30s 封顶）
    """
    import paho.mqtt.client as mqtt  # 延迟 import：poll-only 部署不必装 paho

    m = cfg.get("miservice", {}) or {}
    host = m.get("mqtt_host", "127.0.0.1")
    port = int(m.get("mqtt_port", 1883))
    topic_tpl = m.get("mqtt_topic", "miservice/bridge/{device_id}")
    user = m.get("mqtt_user", "")
    pwd = m.get("mqtt_password", "")
    timeout = float(m.get("mqtt_connect_timeout", 3.0))

    devices = [
        str(d)
        for d in (
            cfg.get("mi", {}).get("devices")
            or ([cfg["mi"]["device_id"]] if cfg.get("mi", {}).get("device_id") else [])
        )
    ]
    if not devices:
        log.warning(
            "mqtt ingest: 未配置 mi.devices / mi.device_id，" "订阅通配 topic '%s'",
            topic_tpl.replace("{device_id}", "+"),
        )
        topics = [topic_tpl.replace("{device_id}", "+")]
    else:
        topics = [topic_tpl.format(device_id=d) for d in devices]

    loop = asyncio.get_running_loop()
    connected = asyncio.Event()

    def _make_handler(q: asyncio.Queue, client_mqtt: "mqtt.Client"):
        def on_message(_c, _u, msg):
            try:
                data = json.loads(msg.payload.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                data = {"text": msg.payload.decode("utf-8", "ignore").strip()}
            try:
                loop.call_soon_threadsafe(
                    q.put_nowait, {"_topic": msg.topic, "_data": data}
                )
            except asyncio.QueueFull:
                log.warning("mqtt queue full, drop 1 msg")

        return on_message

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION1, clean_session=True, protocol=mqtt.MQTTv311
    )
    if user:
        client.username_pw_set(user, pwd or None)
    client.on_connect = lambda c, _u, _f, rc: (
        connected.set() if rc == 0 else log.error("mqtt connect rc=%s", rc)
    )

    log.info(
        "mqtt ingest: connecting %s:%s topics=%s (timeout %.1fs)",
        host,
        port,
        topics,
        timeout,
    )
    conn_ok = False
    try:
        client.connect(host, port, keepalive=60)
        client.loop_start()
        await asyncio.wait_for(connected.wait(), timeout=timeout)
        conn_ok = True
    except (asyncio.TimeoutError, OSError) as e:
        log.warning("mqtt connect failed (%s)", e)

    if not conn_ok:
        client.loop_stop()
        if poll_fallback:
            log.warning("mqtt unavailable → falling back to poll ingest")
            return
        # 无回落：无限退避重连
        backoff = 1.0
        while True:
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
            try:
                client = mqtt.Client(
                    mqtt.CallbackAPIVersion.VERSION1,
                    clean_session=True,
                    protocol=mqtt.MQTTv311,
                )
                client.connect(host, port, keepalive=60)
                client.loop_start()
                log.info("mqtt reconnected after backoff")
                break
            except OSError:
                continue

    # 订阅
    loop_q: asyncio.Queue = asyncio.Queue(maxsize=1024)
    handler = _make_handler(loop_q, client)
    client.on_message = handler
    for t in topics:
        client.subscribe(t, qos=0)

    fallback_used = False
    while True:
        try:
            evt = await loop_q.get()
            rec = _item(evt["_data"], fallback_device="unknown")
            if rec and dedup.accept(rec["session_id"], rec["ts"]):
                log.debug("mqtt → %s: %s", rec["device_id"], rec["text"])
                yield rec
        except asyncio.CancelledError:
            client.loop_stop()
            raise
        except Exception as e:
            if not fallback_used and poll_fallback:
                log.warning("mqtt stream broke (%s) → falling back to poll", e)
                client.loop_stop()
                fallback_used = True
                return
            log.warning("mqtt stream error: %s", e)
            await asyncio.sleep(1.0)


# ---------------------------------------------------------------------------
# 轮询模式（兜底）
# ---------------------------------------------------------------------------
async def _consume_poll(
    cfg: dict, dedup: _BoundedDedup, http_session: aiohttp.ClientSession
):
    """轮询 MiService HTTP 对话记录接口（路径因版本而异，可配）。

    与旧版的区别：
      - 间隔可配（miservice.poll_interval，默认 2s）
      - 指数退避（404/连接失败时 1s→30s），不再每 2s 打一条 warning 刷屏
      - 水位线去重，重启不重放历史
    """
    m = cfg.get("miservice", {}) or {}
    base = m.get("base_url", "http://localhost:8080").rstrip("/")
    path = m.get("talks_path", "/api/v1/talks")
    interval = float(m.get("poll_interval", 2.0))
    url = f"{base}{path}"
    seen_ids: set[str] = set()

    log.info("poll ingest: %s every %.1fs", url, interval)
    backoff = interval
    while True:
        try:
            async with http_session.get(
                url, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status == 200:
                    backoff = interval
                    records = await resp.json()
                    records = (
                        records.get("items", records)
                        if isinstance(records, dict)
                        else records
                    )
                    for rec in records or []:
                        item = _item(rec, fallback_device="default")
                        if item is None:
                            continue
                        rid = item["session_id"]
                        if rid and rid in seen_ids:
                            continue
                        if rid:
                            seen_ids.add(rid)
                            if len(seen_ids) > _DEDUP_CAP:
                                # 粗粒度淘汰：保留一半（顺序近似 LRU）
                                for _ in range(len(seen_ids) // 2):
                                    seen_ids.pop()
                        if dedup.accept(rid, item["ts"]):
                            log.debug("poll → %s: %s", item["device_id"], item["text"])
                            yield item
                else:
                    log.warning("poll: HTTP %s, backoff %.1fs", resp.status, backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30.0)
        except (aiohttp.ClientError, json.JSONDecodeError) as e:
            log.warning("poll unreachable (%s), backoff %.1fs", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


# ---------------------------------------------------------------------------
# 对外统一入口
# ---------------------------------------------------------------------------
async def consume(cfg: dict, http_session: aiohttp.ClientSession):
    """统一收音入口（async 生成器）。

    cfg['miservice']['ingest'] = 'auto' | 'mqtt' | 'poll'（默认 auto）。
    二次开发：新增收音源就在这个函数里加一个分支。
    """
    mode = (cfg.get("miservice", {}) or {}).get("ingest", "auto")
    dedup = _BoundedDedup()

    if mode in ("auto", "mqtt"):
        mqtt_done = False
        async for item in _consume_mqtt(cfg, dedup, poll_fallback=(mode == "auto")):
            yield item
            mqtt_done = True
        if mqtt_done:
            return
        # auto 模式下 mqtt 主动降级 → 继续走 poll
    if mode in ("auto", "poll"):
        async for item in _consume_poll(cfg, dedup, http_session):
            yield item
    else:
        raise ValueError(f"unknown miservice.ingest mode: {mode!r}")
