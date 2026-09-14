# XiaoAi-LLM-Router（小爱同学全能大模型网关）

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/Docker-Compose-green.svg" alt="Docker">
  <img src="https://img.shields.io/badge/LiteLLM-Any%20Model-purple.svg" alt="LiteLLM">
  <img src="https://img.shields.io/badge/Privacy-Local%20First-orange.svg" alt="Privacy">
  <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License">
  <img src="https://img.shields.io/badge/Star-If%20You%20Like%20It-red.svg" alt="Star">
</p>

> **一键把家里那台"只会说天气"的老旧小爱同学，升级成 DeepSeek / Ollama 智能管家。**
> 不用拆机、不用刷固件、不用改硬件——它只是在你现有的 MiService 网关旁边，
> 多了一条"AI 回路"：小爱收音 → LLM 思考 → TTS 说回。

## ✨ 这是什么？

一句话：**生态整合，不从零造轮子**。

小爱同学(收音) → MiService → XiaoAi-LLM-Router → LiteLLM/REST → DeepSeek / Ollama / OpenAI → Mi TTS → 小爱同学(播放)

| 组件 | 我们复用了什么 | 我们只写了什么 |
|------|---------------|----------------|
| 收音 | MiService 拦截的对话流 | 唤醒词过滤（"请问" / "深思"） |
| 思考 | LiteLLM（100+ 供应商统一接口） | 多轮对话记忆的 session 管理 |
| 嘴 | 小米 MiTTS 接口 | 把 LLM 回复切成短句播回 |

**核心特性：**

- 多模型无缝切换：一个 YAML 配置切换 DeepSeek / Ollama 本地模型 / OpenAI / Claude，同一套唤醒词、同一套记忆，零代码改动。
- 多轮对话记忆：按设备 + 时间窗自动维护上下文，"它刚才说的那个东西再大一点"这种指代问题也能接住（可配 TTL，默认 10 分钟）。
- 本地部署，隐私保护：Ollama 模式下整条链路不离开局域网；你的对话不会经过任何第三方服务器（云端模式除外）。
- 一条命令部署：docker compose up -d，连上你现有的 MiService 即可。

## 🚀 30 秒部署

```bash
# 1. 克隆仓库
git clone https://github.com/yourname/xiaoai-llm-router.git && cd xiaoai-llm-router

# 2. 复制并编辑配置（填小米账号、设备号、API Key）
cp config.example.yaml config.yaml
nano config.yaml

# 3. 启动（前提：MiService 已在本机或同网段跑着）
docker compose up -d

# 4. 看日志确认握手
docker compose logs -f router
```

对着小爱说：

```
小爱同学，请深入思考：为什么月亮晚上才上班？
```

小爱会"接"到回答，然后用它自己的音色念给你听。✅

## ⚙️ 配置说明（config.yaml 摘录）

```yaml
mi:
  username: "your_mi_account"        # 小米账号（MiService 同款）
  password: "your_mi_password"
  device_id: "1A2B3C4D-xxxx"        # 在 MiService 的 device 列表里查

llm:
  default_provider: deepseek         # deepseek | ollama | openai | ...（LiteLLM 支持的均可）
  deepseek:
    api_key: "sk-xxxxxxxx"
    model: "deepseek-chat"
  ollama:
    base_url: "http://host.docker.internal:11434"   # 容器内访问宿主机 Ollama
    model: "qwen2.5:7b"
  openai:
    api_key: "sk-xxxxxxxx"
    model: "gpt-4o-mini"

session:
  ttl_seconds: 600                  # 多轮记忆窗口
  max_turns: 10                     # 滑动上下文条数

trigger:
  wake_words: ["请问", "深思"]      # 命中任意一个才走 LLM，否则小爱正常应答

tts:
  engine: "mi_tts"                 # 小米 TTS 接口；也可改成 edge-tts / say
```

## 🧩 二次开发指引

main.py 被刻意写成"胶水"——四个纯函数模块拼装，每个都能单独替换：

miservice.py（收音 & 唤醒词）→ llm_router.py（LiteLLM 统一出口）→ memory.py（多轮 session）→ mi_tts.py（小米 TTS 播放）

- 换收音源（比如加一个 WebSocket 推流）：改 miservice.py 的 listen()，保持"产出一个 dict（text, device_id）"即可。
- 换模型：不需要改代码，llm_router.py 里 provider 是配置驱动的。
- 加意图（"深思"时自动附带 system prompt）：在 trigger 里扩展 wake_words 的字典结构即可，main.py 里有注释标出这个钩子。

## 📈 路线图

- 语音打断（barge-in）：用户说话时立即掐断 TTS 播放
- 工具调用：让"深思"模式可以调日历 / 天气 API
- 多房间：按 room 隔离 session 与音色
- Ollama 流式输出 + 分句 TTS 边想边说

## 🤝 致谢与依赖

- MiService — 小爱同学 API 逆向（github.com/m1cnter/MiService）
- LiteLLM — 100+ LLM 统一接口（github.com/BerriAI/litellm）
- 小米 Mi TTS 开放接口

本项目 MIT 协议，放心用。Star ⭐ 是最好的鼓励。

---

# Xiaomi #DeepSeek #Ollama #LiteLLM #SmartHome #LLM #IoT #Docker
