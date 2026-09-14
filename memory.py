"""
【模块 3】memory.py —— 多轮对话记忆
====================================
职责：按设备维护一个滑动窗口 session，让"它刚才说的那个再大点"接得住。

设计选择（都写在注释里，方便你改）：
  - 进程内 dict 存储：网关是单实例部署（一个家庭一台），够用且零依赖；
    想多实例 → 把 MemoryStore 换成 Redis 实现，接口不变。
  - TTL：超过 ttl 秒没说话就清零（小爱音箱隔几天再用，记忆不串场）。
  - max_turns：只保留最近 N 轮，防止把上周的对话喂给模型浪费 token。

二次开发：
  - 按房间隔离：device_id 改成 "room:device" 复合键。
  - 向量记忆：append() 里顺手 embedding 一次，to_llm_messages() 时 RAG。
"""

import time
from collections import deque


class MemorySession:
    """一台设备的对话记忆（user/assistant 成对出现）。"""

    def __init__(self, max_turns: int = 10):
        # 双端队列：append 右进、popleft 左出，天然滑动窗口
        self.turns: deque[dict] = deque(maxlen=max_turns)
        self.last_active = time.time()

    def append(self, user: str, assistant: str) -> None:
        self.turns.append({"role": "user", "content": user})
        self.turns.append({"role": "assistant", "content": assistant})
        self.last_active = time.time()

    def to_llm_messages(self) -> list[dict]:
        """转成 LiteLLM/OpenAI 的 messages 格式（不含当前这句，由 router 追加）。"""
        return list(self.turns)

    def seconds_since_active(self) -> float:
        return time.time() - self.last_active


class MemoryStore:
    """device_id → MemorySession 的全局存储（进程内，线程安全由 asyncio 单线程保证）。"""

    def __init__(self, ttl: int = 600, max_turns: int = 10):
        self.ttl = ttl
        self.max_turns = max_turns
        self._sessions: dict[str, MemorySession] = {}

    def get(self, device_id: str) -> MemorySession:
        s = self._sessions.get(device_id)
        if s is None:
            s = MemorySession(max_turns=self.max_turns)
            self._sessions[device_id] = s
        # TTL 过期 → 悄悄重建（不清理老 key 也行，重建即可）
        if s.seconds_since_active() > self.ttl:
            s.turns.clear()
            s.last_active = time.time()
        return s

    # 运维钩子：docker exec 进来想 dump 当前所有记忆？加个 CLI 调这个
    def snapshot(self) -> dict[str, dict]:
        return {d: {"turns": list(s.turns)} for d, s in self._sessions.items()}
