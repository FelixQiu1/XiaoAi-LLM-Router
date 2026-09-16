"""最小测试集：分句 / 唤醒词 / 记忆语义。跑法: python -m pytest -q（或 python tests/test_units.py）"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mi_tts import split_sentences
from main import match_wake, resolve_system_prompt
from memory import MemorySession, MemoryStore


def test_split_basic():
    out = split_sentences("你好。今天天气怎么样？", 60)
    assert out == ["你好。", "今天天气怎么样？"], out


def test_split_empty():
    assert split_sentences("", 60) == []
    assert split_sentences("   ", 60) == []


def test_split_long_comma_cut():
    text = "，".join(["这是一个很长的分句" * 3] * 8)  # 远超 60 字
    out = split_sentences(text, 60)
    assert len(out) > 1
    assert all(len(s) <= 70 for s in out), out


def test_wake_match_plain():
    words = ["问问 AI", "深思"]
    assert match_wake(words, "小爱同学，问问 AI 今天吃什么") == "问问 AI"
    assert match_wake(words, "请打开客厅灯") is None


def test_wake_match_dict_form():
    words = [{"word": "深思", "prefix": "请先给推理步骤"}]
    assert match_wake(words, "深思：1+1") == "深思"
    sp = resolve_system_prompt(words, "深思", "DEFAULT")
    assert "推理步骤" in sp and "DEFAULT" in sp


def test_memory_turn_semantics():
    # v1.1 语义：max_turns=3 → 最多保留 3 轮（6 条 message）
    s = MemorySession(max_turns=3)
    for i in range(5):
        s.append(f"u{i}", f"a{i}")
    msgs = s.to_llm_messages()
    assert len(msgs) == 6, len(msgs)
    assert msgs[0] == {"role": "user", "content": "u2"}   # 前两轮被挤掉
    assert msgs[-1] == {"role": "assistant", "content": "a4"}


def test_memory_pending_not_polluted():
    s = MemorySession(max_turns=5)
    s.append_pending("我问了但没答上")
    msgs = s.to_llm_messages()
    assert msgs[1]["content"].startswith("[assistant")   # 占位而非兜底话


def test_memory_store_ttl_purge():
    store = MemoryStore(ttl=0, max_turns=5)   # ttl=0：下次 get 即过期
    d1 = store.get("devA")
    d1.append("x", "y")
    store.purge_expired()
    assert "devA" not in store.snapshot(), "过期 session 应被清理"

# ---- v1.2：机型指令表 / 档位判断 ----
import mi_conversation as conv


def test_tier_l06a_normal():
    t = conv.tier_of("L06A")
    assert t["tier"] == "normal", t
    assert t["tts"] == "5-1" and t["wakeup"] == "5-5"
    assert t["stream"] is False


def test_tier_perfect_has_stream():
    assert conv.tier_of("LX06")["tier"] == "perfect"
    assert conv.tier_of("LX06")["stream"] is True


def test_tier_unknown_falls_back():
    t = conv.tier_of("LXXX")
    assert t["tier"] == "unknown"
    assert t["tts"] == "5-1"          # 默认指令兜底
    assert "PR" in t["note"]


def test_tier_unsupported():
    assert conv.tier_of("SM4")["tier"] == "unsupported"
