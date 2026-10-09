"""
【模块 3】memory.py —— 多轮对话记忆
====================================
职责：按设备维护一个滑动窗口 session，让"它刚才说的那个再大点"接得住。

设计选择（都写在注释里，方便你改）：
  - 进程内 dict 存储：网关是单实例部署（一个家庭一台），够用且零依赖；
    想多实例 → 把 MemoryStore 换成 Redis 实现，接口不变。
  - TTL：超过 ttl 秒没说话就清零（小爱音箱隔几天再用，记忆不串场）。
  - max_turns：按"轮"计（一轮 = 1 句 user + 1 句 assistant，共 2 条 message），
    防止把上周的对话喂给模型浪费 token。
    ⚠️ v1.0 的坑：deque(maxlen=max_turns) 里 maxlen 的单位是 message 不是 turn，
    配 10 实际只留 5 轮。v1.1 起内部 ×2，语义与配置名一致。

失败兜底约定（main.py 负责执行）：
  - LLM 调用失败时 **不要** 把兜底话写进 assistant 历史（会污染下一轮上下文），
    只把 user 句记为"未获回答"（见 MemorySession.append_pending）。

二次开发：
  - 按房间隔离：device_id 改成 "room:device" 复合键。
  - 向量记忆：append() 里顺手 embedding 一次，to_llm_messages() 时 RAG。
"""

from __future__ import annotations

import time
from collections import deque


class MemorySession:
    """一台设备的对话记忆（user/assistant 成对出现）。"""

    def __init__(self, max_turns: int = 10):
        # maxlen 单位是 message；max_turns 轮 × 2 = 最大 message 数
        self.turns: deque[dict] = deque(maxlen=max(2, max_turns) * 2)
        self.last_active = time.time()

    def append(self, user: str, assistant: str) -> None:
        self.turns.append({"role": "user", "content": user})
        self.turns.append({"role": "assistant", "content": assistant})
        self.last_active = time.time()

    def append_pending(
        self, user: str, note: str = "[assistant: LLM 调用失败，未回答]"
    ) -> None:
        """LLM 失败时记 user 句 + 占位 assistant（可被模型理解为"这轮没答上"）。
        占位文本刻意简短，避免污染上下文。"""
        self.turns.append({"role": "user", "content": user})
        self.turns.append({"role": "assistant", "content": note})
        self.last_active = time.time()

    def to_llm_messages(self) -> list[dict]:
        """转成 LiteLLM/OpenAI 的 messages 格式（不含当前这句，由 router 追加）。"""
        return list(self.turns)

    def clear(self) -> None:
        self.turns.clear()
        self.last_active = time.time()

    def seconds_since_active(self) -> float:
        return time.time() - self.last_active


class MemoryStore:
    """device_id → MemorySession 的全局存储（进程内，asyncio 单线程保证安全）。

    v1.1：TTL 过期的 session 直接删 key（v1.0 留着对象不清理，
    snapshot() 会带出大量僵尸设备）。
    """

    def __init__(self, ttl: int = 600, max_turns: int = 10):
        self.ttl = ttl
        self.max_turns = max_turns
        self._sessions: dict[str, MemorySession] = {}

    def get(self, device_id: str) -> MemorySession:
        s = self._sessions.get(device_id)
        if s is None:
            s = MemorySession(max_turns=self.max_turns)
            self._sessions[device_id] = s
            return s
        # TTL 过期 → 删掉重建（而不是原地清空，顺带清掉僵尸 key）
        if s.seconds_since_active() > self.ttl:
            del self._sessions[device_id]
            s = MemorySession(max_turns=self.max_turns)
            self._sessions[device_id] = s
        return s

    def purge_expired(self) -> int:
        """运维钩子：周期性清理过期 session（main 可每 60s 调一次）。
        返回被清理的 key 数。"""
        dead = [
            d for d, s in self._sessions.items() if s.seconds_since_active() > self.ttl
        ]
        for d in dead:
            del self._sessions[d]
        return len(dead)

    # 运维钩子：docker exec 进来想 dump 当前所有记忆？加个 CLI 调这个
    def snapshot(self) -> dict[str, dict]:
        return {d: {"turns": list(s.turns)} for d, s in self._sessions.items()}
