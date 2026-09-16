# XiaoAi-LLM-Router（小爱同学全能大模型网关）

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/Docker-Compose-green.svg" alt="Docker">
  <img src="https://img.shields.io/badge/LiteLLM-Any%20Model-purple.svg" alt="LiteLLM">
  <img src="https://img.shields.io/badge/Ingest-MQTT%20%2B%20Poll-orange.svg" alt="Ingest">
  <img src="https://img.shields.io/badge/Privacy-Local%20First-red.svg" alt="Privacy">
  <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License">
</p>

> **一键把家里那台"只会说天气"的老旧小爱同学，升级成 DeepSeek / Ollama 智能管家。**
> 不用拆机、不用刷固件、不用改硬件——它只是在你现有的 MiService 网关旁边，
> 多了一条"AI 回路"：小爱收音 → LLM 思考 → TTS 说回。

## ✨ 这是什么？

一句话：**生态整合，不从零造轮子**。

```
小爱同学(收音) ─MiService MQTT/poll─▶ XiaoAi-LLM-Router ─LiteLLM─▶ DeepSeek / Ollama / OpenAI
小爱同学(播放) ◀─小米 TTS(短句逐句播)────────────────────────────────┘
```

| 组件 | 我们复用了什么 | 我们只写了什么 |
|------|---------------|----------------|
| 收音 | MiService MQTT Bridge（延迟 <1s），HTTP 轮询兜底 | 唤醒词过滤（"问问 AI" / "深思"） |
| 思考 | LiteLLM（100+ 供应商统一接口） | 多轮对话记忆 + 流式"边想边说" |
| 嘴 | 小米 Mi TTS 接口 | 把 LLM 回复切成短句逐句播回 |

**核心特性：**

- 🔄 **多模型无缝切换**：一个 YAML 配置切换 DeepSeek / Ollama 本地模型 / OpenAI / Claude，
  同一套唤醒词、同一套记忆，零代码改动。
- 🧠 **多轮对话记忆**：按设备 + 时间窗自动维护上下文（TTL 默认 10 分钟），
  "它刚才说的那个东西再大一点"这种指代问题也能接住。
- ⚡ **边想边说（流式）**：模型每攒够一个短句立即 TTS，小爱的"思考感"从 15s 降到 2s。
- 🔒 **本地部署，隐私保护**：Ollama 模式下整条链路不离开局域网，对话不出户。
- 🐳 **一条命令部署**：docker compose up -d --build。
- 🧪 **演示模式**：python main.py --demo 不依赖 MiService，终端输入也能跑通全链路。

## 🚀 30 秒部署

```bash
# 1. 克隆仓库
git clone https://github.com/FelixQiu1/XiaoAi-LLM-Router.git && cd XiaoAi-LLM-Router

# 2. 复制并编辑配置（小米账号、设备号、API Key；密钥建议走 .env）
cp config.example.yaml config.yaml
cp .env.example .env && nano .env

# 3. 构建并启动（前提：MiService 已在本机或同网段跑着）
docker compose up -d --build

# 4. 看日志确认握手（应看到 mqtt ingest: connecting ...）
docker compose logs -f router
```

对着小爱说：

```
小爱同学，问问 AI：为什么月亮晚上才上班？
```

小爱会"接"到 DeepSeek/Ollama 的回答，并用自己的音色念给你听。✅

**没有小米设备？** 跑 python main.py --demo，从终端打字 → 走完整条 LLM 链路，
TTS 部分打印文字（装了 edge-tts 能直接出声）。

## ⚙️ 配置速查（config.yaml）

```yaml
miservice:
  ingest: "auto"                # auto=先连 MQTT，3s 连不上自动回落 HTTP 轮询
  mqtt_host: "127.0.0.1"       # 容器部署时改成 MiService 所在主机
  base_url: "http://localhost:8080"

llm:
  default_provider: deepseek   # deepseek | ollama | openai（LiteLLM 支持的均可）
  stream: true                 # 边想边说（推荐开）

trigger:
  wake_words: ["问问 AI", "深思"]
  # ⚠️ 不要加 "请问"：它出现在小爱 99% 的指令里，网关会变成抢答的复读机

session:
  ttl_seconds: 600             # 多轮记忆窗口（同一设备 10 分钟不聊就清零）
```

完整模板在 config.example.yaml，每段都有中文注释。

## 🧩 二次开发指引

main.py 被刻意写成"胶水"——四个纯函数模块拼装，每个都能单独替换：

```
miservice_glue.py(收音:MQTT+poll) → main.py(唤醒判定/流水线) → llm_router.py(LiteLLM 统一出口)
                                                        ↓
                                    memory.py(多轮 session) → mi_tts.py(TTS 短句播回)
```

- **换收音源**（WebSocket / 本地麦克风 ASR）：实现 consume()，
  保持产出 {"text","device_id","session_id","ts"} 契约即可，main.py 只改一行分发。
- **换模型**：零代码，llm.<provider> 配置驱动。
- **加意图**（"深思"自动附 system prompt）：wake_words 用字典形态
  {word: "深思", prefix: "..."}，resolve_system_prompt() 自动拼。
- **加环节**（敏感词过滤 / 工具调用）：main.py handle() 里 ①~⑤ 之间加一行。

## 📈 路线图

- [ ] 语音打断（barge-in）：新句子到达时立即掐断 TTS 播放队列
- [ ] 工具调用：让"深思"模式可以调日历 / 天气 API
- [ ] 多房间：按 room:device 复合键隔离 memory 与音色（Redis 后端选项）
- [ ] GitHub 一键 Action 部署

## 🤝 致谢与依赖

- [MiService](https://github.com/m1cnter/MiService) — 小爱同学 API 逆向（MQTT Bridge）
- [LiteLLM](https://github.com/BerriAI/litellm) — 100+ LLM 统一接口

MIT 协议，放心用。Star ⭐ 是最好的鼓励。

---

`# Xiaomi #DeepSeek #Ollama #LiteLLM #SmartHome #LLM #IoT #Docker`
