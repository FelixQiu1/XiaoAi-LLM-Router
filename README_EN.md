# XiaoAi-LLM-Router (XiaoAi Speaker Universal LLM Gateway)

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/Docker-Compose-green.svg" alt="Docker">
  <img src="https://img.shields.io/badge/LiteLLM-Any%20Model-purple.svg" alt="LiteLLM">
  <img src="https://img.shields.io/badge/Ingest-Conversation%20%2B%20Poll-orange.svg" alt="Ingest">
  <img src="https://img.shields.io/badge/Privacy-Local%20First-red.svg" alt="Privacy">
  <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License">
</p>

**[中文](README.md) | [English](README_EN.md)**

> **Turn your aging XiaoAi speaker — the one that only knows the weather — into a DeepSeek / Ollama smart butler with one click.**
> No disassembly, no flashing firmware, no hardware mods. It simply adds an "AI circuit" next to your existing MiService Gateway:
> XiaoAi listens → LLM thinks → TTS speaks back.

## ✨ What is this?

One line: **ecosystem integration, not reinventing the wheel.**

```
XiaoAi Speaker (audio in) ─MiService conversation/poll─▶ XiaoAi-LLM-Router ─LiteLLM─▶ DeepSeek / Ollama / OpenAI
XiaoAi Speaker (audio out) ◀─Mi TTS (sentence-chunked playback)────────────────────────┘
```

| Component | What we reuse | What we only wrote |
|-----------|---------------|--------------------|
| Audio in | MiService conversation intercept (model-agnostic) + HTTP polling fallback | Wake-word filter ("ask the AI" / "deep think") |
| Thinking | LiteLLM (100+ providers, unified interface) | Multi-turn memory + Streamed Thinking & Speaking (TTS) |
| Audio out | Mi TTS API | Chunking LLM replies into short spoken sentences |

**Key features:**

- 🔄 **Seamless multi-model switching**: one YAML config switches between DeepSeek / local Ollama / OpenAI / Claude —
  same wake words, same memory, zero code changes.
- 🧠 **Multi-turn conversation memory**: context is maintained per device + time window (TTL defaults to 10 min),
  so anaphoric questions like "make that thing it mentioned earlier bigger" still land.
- ⚡ **Streamed Thinking & Speaking (TTS)**: as soon as the model accumulates a short sentence, it's spoken immediately —
  XiaoAi's "thinking" feels like 2s instead of 15s.
- 🔒 **Local-first deployment, privacy**: in Ollama mode the entire chain stays on your LAN; conversations never leave the house
  (cloud mode excepted).
- 🐳 **One-command deploy**: docker compose up -d --build.
- 🧪 **Demo mode**: python main.py --demo runs the full LLM chain from your terminal without any Xiaomi device.

## 🚀 30-Second Deploy

```bash
# 1. Clone
git clone https://github.com/FelixQiu1/XiaoAi-LLM-Router.git && cd XiaoAi-LLM-Router

# 2. Copy and edit config (Mi account, device id, API key; keys recommended via .env)
cp config.example.yaml config.yaml
cp .env.example .env && nano .env

# 3. Build & start
docker compose up -d --build

# 4. Watch logs for handshake
docker compose logs -f router
```

Say to XiaoAi:

```
XiaoAi, ask the AI: why does the moon only work at night?
```

XiaoAi "catches" the DeepSeek/Ollama answer and speaks it in her own voice. ✅

**No Xiaomi device?** Run python main.py --demo — type from your terminal, full LLM chain,
TTS prints text (or real audio if you installed edge-tts).

**Which speakers are supported?** See [docs/SUPPORTED_DEVICES.md](docs/SUPPORTED_DEVICES.md) —
rule of thumb: any Wi-Fi speaker that appears in the Mi Home app and answers "XiaoAi" works;
pure-BT portable editions do not.

## ⚙️ Config Cheatsheet (config.yaml)

```yaml
miservice:
  ingest: "conversation"      # conversation | poll | auto (conversation first, fall back to poll)
  base_url: "http://localhost:8080"

llm:
  default_provider: deepseek # deepseek | ollama | openai (anything LiteLLM supports)
  stream: true               # Streamed Thinking & Speaking (recommended on)

trigger:
  wake_words: ["ask the AI", "deep think"]
  # ⚠️ Do NOT add "may I ask" (请问): it appears in 99% of XiaoAi commands and the gateway becomes a repeat-echo machine

session:
  ttl_seconds: 600           # multi-turn memory window (10 min idle → reset)
```

Full template with comments in config.example.yaml.

## 🔒 Security & Privacy

- **LAN isolation**: in Ollama mode, audio → LLM → TTS never leaves your LAN.
- **Credential handling**: API keys live in .env / environment variables, never in config.yaml or git
  (.env and config.yaml are git-ignored). The MiService service token is cached to ~/.mi.token locally only.
- **No conversation data leaves your machine**: this router only *relays* LLM answers to your speaker;
  the MiService library does not store dialog history on a third-party server.

## 🧩 Extending (for contributors)

main.py is deliberately "glue" — four pure-function modules, each replaceable:

```
mi_conversation.py (model-agnostic ingest via MiService lib)
miservice_glue.py (MQTT/poll fallback) → main.py (wake gate / pipeline) → llm_router.py (LiteLLM unified exit)
                                                        ↓
                                    memory.py (per-device sessions) → mi_tts.py (chunked TTS)
```

- **Swap the audio source** (WebSocket / local-mic ASR): implement a consume() that yields
  {"text","device_id","request_id","ts"} — main.py only re-points one dispatch line.
- **Swap the model**: zero code, config-driven via llm.<provider>.
- **Add intent** ("deep think" attaches a system prompt): use the dict form of wake_words,
  {word: "deep think", prefix: "..."} — resolve_system_prompt() merges it automatically.
- **Add a stage** (content filter / tool calling): insert one line between steps ①–⑤ in main.py handle().

## 📈 Roadmap

- [ ] Voice interruption (barge-in): drop the TTS queue the moment a new sentence arrives
- [ ] Tool calling: let "deep think" mode hit calendar / weather APIs
- [ ] Multi-room: isolate memory & voice by room:device composite key (Redis backend option)
- [ ] One-click GitHub Action deploy

## 🤝 Credits & Dependencies

- [MiService](https://github.com/Yonsm/MiService) — XiaoAi cloud service (Python library + CLI)
- [LiteLLM](https://github.com/BerriAI/litellm) — 100+ LLM unified interface
- [mi-gpt](https://github.com/idootop/mi-gpt) — speaker compatibility table
- [xiaogpt](https://github.com/yihong0618/xiaogpt) — per-hardware command dictionary

MIT license. Star ⭐ is the best encouragement.

---

# Xiaomi #DeepSeek #Ollama #LiteLLM #SmartHome #LLM #IoT #Docker

## Related Docs

- Speaker support list: [docs/SUPPORTED_DEVICES.md](docs/SUPPORTED_DEVICES.md)
- v1.2 release notes: [docs/GITHUB_RELEASE_v1.2.md](docs/GITHUB_RELEASE_v1.2.md)
