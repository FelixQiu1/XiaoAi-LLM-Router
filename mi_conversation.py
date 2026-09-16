"""
【模块 1b】mi_conversation.py —— 机型无关收音 + 设备自检（基于 MiService Python 库）
=================================================================================

为什么单独一个模块？
  v1.1 押注 MiService 的 MQTT Bridge（可选插件，topic 版本漂移、默认不开）。
  v1.2 改用整个生态（xiaogpt / mi-gpt）验证过的最通用方案：
  直接依赖 PyPI 上的 MiService Python 库，用它现成的：
    - MiNAService.device_list()    列出账号下所有小爱音箱（含 hardware 型号）
    - MiNAService.get_latest_ask() 拦截小爱最近一轮"用户 query + 小爱回复"
    - MiNAService.text_to_speech() 让指定音箱念出 LLM 回复
  三个能力串起来 = 机型无关的收音源 + TTS 嘴。

与 miservice_glue.py（模块 1）的关系：
  - 本模块（conversation）是默认收音源，依赖 MiService 库
  - 模块 1（poll/mqtt）是 fallback，不依赖 MiService 库（纯 HTTP/MQTT）
  - config miservice.ingest: conversation | poll | auto（auto = 先 conversation，挂了落 poll）

机型指令表 HARDWARE_COMMAND_DICT（v1.2 内置）：
  来源：mi-gpt 官方兼容表 + xiaogpt config.py，两者互相印证。
  用途：启动时查 device_list 拿到 hardware 字段，对号入座判断档位；
  查不到的机型走默认指令 + warning，提示用户贡献回仓库（一行配置）。

二次开发：
  - 加新机型：在 HARDWARE_COMMAND_DICT 里加一行 "LXXX": ("5-1","5-5") 即可
  - 换 TTS 后端（edge-tts 生成 mp3 再 play_by_url）：改 MiSpeaker.tts() 一处
"""

from __future__ import annotations

import logging
import time

log = logging.getLogger("xiaoai-router.mina")

# ---------------------------------------------------------------------------
# 机型指令表：hardware → (tts_command, wakeup_command, supports_stream)
#   tts_command      : 播报 TTS 的 MIoT 指令（如 "5-1" / "7-3" / "3-1"）
#   wakeup_command   : 唤醒/打断指令（部分机型用）
#   supports_stream  : 是否支持"连续对话"（streamResponse，L06A 这档为 False）
# 数据核对：mi-gpt docs/compatibility.md + xiaogpt config.py HARDWARE_COMMAND_DICT
# ---------------------------------------------------------------------------
HARDWARE_COMMAND_DICT: dict[str, tuple[str, str, bool]] = {
    # ---------- ✅ 完美支持（含实验性连续对话） ----------
    "OH2P":  ("7-3", "7-1", True),
    "OH2":   ("5-3", "5-1", True),
    "LX06":  ("5-1", "5-3", True),
    "S12":   ("5-1", "5-3", True),
    "S12A":  ("5-1", "5-5", True),
    "L15A":  ("7-3", "7-1", True),
    "LX5A":  ("5-1", "5-5", True),
    "LX05":  ("5-1", "5-3", True),
    "LX05A": ("5-1", "5-5", True),
    "X10A":  ("7-3", "7-1", True),
    "L17A":  ("7-3", "7-1", True),
    # ---------- 🚗 正常支持（TTS 问答链路完整；关 streamResponse） ----------
    "L06A":  ("5-1", "5-5", False),   # 小爱音箱 / Redmi 小爱音箱（零售 L607/L607A）
    "LX01":  ("5-1", "5-5", False),
    "L05B":  ("5-3", "5-1", False),
    "L05C":  ("5-3", "5-4", False),
    "L07A":  ("5-1", "5-5", False),
    "L09A":  ("3-1", "3-2", False),
    "LX04":  ("5-1", "5-4", False),
    "X4B":   ("5-3", "5-1", False),
    "ASX4B": ("5-3", "5-1", False),
    "X6A":   ("7-3", "7-1", False),
    "X08E":  ("7-3", "7-1", False),
    "X8F":   ("7-3", "7-1", False),
}

# 完全不支持（云协议层不开放 / 纯蓝牙无云小爱）—— 启动自检时直接提示换机型
UNSUPPORTED_HARDWARE: dict[str, str] = {
    "SM4": "小米小爱音箱 HD：云协议层不开放",
    # 纯蓝牙随身版没有固定 hardware 编码，靠"不在 device_list 里"识别
}

DEFAULT_COMMAND: tuple[str, str, bool] = ("5-1", "5-5", False)


def tier_of(hardware: str) -> dict:
    """返回机型档位信息（供 --list-devices 打印 / 启动自检用）。

    返回: {"hardware":..., "tier": "perfect|normal|unknown|unsupported",
           "tts":..., "wakeup":..., "stream":..., "note":...}
    """
    hw = (hardware or "").upper().strip()
    if hw in UNSUPPORTED_HARDWARE:
        return {"hardware": hw, "tier": "unsupported",
                "note": UNSUPPORTED_HARDWARE[hw]}
    if hw in HARDWARE_COMMAND_DICT:
        tts, wk, stream = HARDWARE_COMMAND_DICT[hw]
        tier = "perfect" if stream else "normal"
        return {"hardware": hw, "tier": tier, "tts": tts, "wakeup": wk,
                "stream": stream, "note": ""}
    tts, wk, _ = DEFAULT_COMMAND
    return {"hardware": hw, "tier": "unknown", "tts": tts, "wakeup": wk,
            "stream": False,
            "note": "指令表未收录，按默认 5-1 走；可贡献 PR 补进 HARDWARE_COMMAND_DICT"}


# ---------------------------------------------------------------------------
# MiNA 封装：把 MiService 库的 MiAccount + MiNAService 粘成"音箱控制器"
# ---------------------------------------------------------------------------
class MiSpeaker:
    """一个小米账号 + 目标音箱，负责 收音(get_latest_ask) + 播报(text_to_speech)。

    用法（main.py 注入）：
        sp = await MiSpeaker.connect(cfg)
        msg = await sp.latest_ask()          # → {"text","device_id","request_id","ts"} | None
        await sp.tts(device_id, "回复文本")

    所有方法都是 async（MiService 库本身是 asyncio 实现）。
    二次开发：换 TTS 后端（edge-tts 生成 mp3 + play_by_url）只改 tts() 一处。
    """

    def __init__(self, account, mina, cfg: dict):
        self.account = account
        self.mina = mina
        self.cfg = cfg

    @classmethod
    async def connect(cls, cfg: dict) -> "MiSpeaker":
        """登录小米账号，建 MiNAService。失败抛 RuntimeError（main 兜底）。"""
        import miservice  # 延迟 import：poll-only 部署不必装 miservice
        import aiohttp

        mi = cfg.get("mi", {}) or {}
        user = mi.get("username") or ""
        pwd = mi.get("password") or ""
        if not user or not pwd:
            raise RuntimeError(
                "小米账号未配置：请设置 MI_USER / MI_PASS（.env 或 config.yaml 的 mi 段）。"
                "跑 --list-devices 前需要能登录小米云。"
            )

        async with aiohttp.ClientSession() as session:
            account = miservice.MiAccount(session, user, pwd)
            # 登录小爱云（sid=micoapi），serviceToken 落 ~/.mi.token 可复用
            ok = await account.login("micoapi")
            if not ok:
                raise RuntimeError("小米账号登录失败（检查账号密码 / 是否需 OTP）")
            mina = miservice.MiNAService(account)
            return cls(account, mina, cfg)

    # ---- 收音：拦截小爱最近一轮问答 ----
    async def latest_ask(self, device_id: str | None = None) -> dict | None:
        """拉取小爱最近一轮"用户 query + 小爱回复"。

        device_id 为空 → 自动取 device_list 里第一台小爱音箱。
        返回契约 dict（与 miservice_glue 一致，main.py 只认这个）：
          {"text": 用户说的, "device_id":..., "request_id":..., "ts":...}
        没有新问答 → None。
        """
        did = device_id or await self._first_device()
        if not did:
            return None
        msgs = await self.mina.get_latest_ask(did)
        if not msgs:
            return None
        # 取最新一条（list 按时间倒序或正序都兼容：取 timestamp 最大的）
        m = max(msgs, key=lambda x: x.get("timestamp_ms", 0))
        answers = (m.get("response") or {}).get("answer") or []
        if not answers:
            return None
        q = next((a.get("question") for a in answers if a.get("question")), "")
        if not q:
            return None
        return {
            "text": q.strip(),
            "device_id": did,
            "request_id": m.get("request_id", ""),
            "ts": m.get("timestamp_ms", 0) / 1000.0,
        }

    async def _first_device(self) -> str | None:
        """device_list 里找第一台小爱音箱（name 含 '小爱' 或 model 属 speaker）。"""
        devices = await self.mina.device_list() or []
        for d in devices:
            name = (d.get("name") or d.get("nickname") or "")
            model = (d.get("model") or d.get("hardware") or "")
            did = str(d.get("did") or d.get("device_id") or "")
            if did and ("小爱" in name or "speaker" in model.lower() or "xiaoai" in model.lower()):
                return did
        if devices:
            return str(devices[0].get("did") or devices[0].get("device_id") or "")
        return None

    # ---- 自检：列出账号下所有小爱音箱 + 档位（--list-devices）----
    async def list_devices(self) -> list[dict]:
        devices = await self.mina.device_list() or []
        out = []
        for d in devices:
            hw = (d.get("model") or d.get("hardware") or "").upper()
            name = d.get("name") or d.get("nickname") or "?"
            did = str(d.get("did") or d.get("device_id") or "")
            tier = tier_of(hw)
            out.append({"name": name, "did": did, **tier})
        return out

    # ---- 播报：让音箱念出 LLM 回复（v1.2 嘴）----
    async def tts(self, device_id: str, text: str) -> None:
        """text_to_speech 播报。失败只打日志（不抛），念不出不中断对话流。"""
        text = (text or "").strip()
        if not text:
            return
        try:
            ok = await self.mina.text_to_speech(device_id, text)
            log.info("tts → %s: %s", device_id, text[:30] + ("…" if len(text) > 30 else ""))
            if not ok:
                log.warning("tts returned False for device %s", device_id)
        except Exception as e:
            log.error("tts failed: %s", e)