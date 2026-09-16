# 📢 v1.2.0 Release Notes（可直接贴到 GitHub Release）

> 一句话：**收音源重做为机型无关方案 + 内置 23 款小爱音箱指令表 + 部署前自检子命令，L06A（L607A）等 12 款音箱开箱即用。**
>
> 本次更新解决 v1.1 的 3 个"跑不动"问题：
> 1. v1.1 押注的 MiService MQTT Bridge 是可选插件，默认不开启、topic 随版本漂移 → 大多数用户部署后 404 循环
> 2. 没有 LICENSE（GitHub 默认"保留所有权利"，35 star 项目的法律裸奔）
> 3. 收音源"轮询不存在的对话日志接口" → 已修正为 MiService 官方 Python 库的真实 API

---

## ✨ 本次更新（v1.1 → v1.2）

### 1. 收音源重做：机型无关的"小爱问答拦截"

v1.2 直接依赖 PyPI 上的 **MiService 官方 Python 库**（`pip3 install miservice`，已验证可装），用它现成的三个能力串成收音链路：

```
小爱音箱 ──真实提问──▶ 小米云 ASR ──▶ 小爱原生应答
      ▲                        │
      │                        ▼
  TTS 播报 ◀── MiNAService.text_to_speech ◀── 拦截 get_latest_ask（request_id 水位线去重）
```

- **`MiNAService.get_latest_ask()`**：拦截小爱最近一轮"用户 query + 回复"，`request_id` 做水位线，重启不重放历史
- **`MiNAService.text_to_speech()`**：LLM 答案直接让音箱念出来（替代 v1.1 猜的 HTTP `/api/v1/tts` 路径）
- **`MiNAService.device_list()`**：列出账号下所有小爱音箱 + 型号，支撑下面的自检子命令
- 收益：**机型无关**——任何在米家 App 里能问"小爱同学"的 Wi-Fi 音箱直接支持，不依赖 MiService 是否开了 MQTT 插件

### 2. 内置 23 款机型指令表（`HARDWARE_COMMAND_DICT`）

TTS / 唤醒 MIoT 指令按机型自动适配（数据来源：mi-gpt 官方兼容表 + xiaogpt config.py，两者互相印证）：

| 档位 | 型号 | 连续对话 |
|---|---|---|
| ✅ 完美 | OH2P / OH2 / LX06 / S12 / S12A / L15A / LX5A / LX05 / LX05A / X10A / L17A | 支持（建议关） |
| 🚗 正常 | **L06A** / LX01 / L05B / L05C / L07A / L09A / LX04 / X4B / ASX4B / X6A / X08E / X8F | 不支持（关 stream） |
| ❌ 不支持 | SM4（小爱音箱 HD）/ 纯蓝牙随身版 | — |

查不到的机型自动走默认指令 `5-1` + 打 warning，提示用户贡献 PR 补进指令表（一行配置）。

### 3. 部署前自检：`--list-devices` 子命令

```bash
python main.py --list-devices          # 列出你小米账号下所有小爱音箱 + 支持档位
python main.py --list-devices --hardware L06A   # 只看某台
```

输出示例：
```
名称                    DID            hardware 档位       备注
客厅小爱                267090026      L06A     🚗 正常    零售 L607/L607A
```

**10 秒内知道自己这台音箱能不能用**，不用再翻文档猜。

### 4. 依赖修正

- `requirements.txt` 加回 `miservice`（v1.0 误以为它不在 PyPI、v1.1 又删掉，v1.2 已 `pip3 install` 验证真实存在）
- `ingest` 配置项三态：`conversation`（默认，MiService 库）| `poll`（HTTP 兜底）| `auto`（先 conversation，挂回落 poll）

### 5. 其他

- 新增 `docs/SUPPORTED_DEVICES.md`：独立机型支持列表 + 3 行快速判断 + 贡献新机型指南
- 测试补 4 个（机型指令表 / 完美档 stream / 未知机型 fallback / SM4 不支持），现共 **12 个单测全过**
- MIT LICENSE 已在 v1.1 补齐

---

## 🔊 支持的小爱音箱型号（完整列表）

> **判断口诀：在米家 App 里能看到、能语音问"小爱同学"的 Wi-Fi 音箱 = 支持；纯蓝牙随身版 = 不支持。**

### 3 行快速判断

```
你的音箱在米家 App 设备列表里吗？ ── 否 → 不支持（纯蓝牙随身版）
            │ 是
它说话时你听到的是"小爱同学"音色吗？ ── 否 → 不支持
            │ 是
查下表：✅/🚗 都能用（🚗 记得关 stream）；❌ 换机型
```

### ✅ 完美支持（TTS 问答 + 实验性连续对话）

| 名称 | 型号 |
|---|---|
| Xiaomi 智能音箱 | OH2 |
| Xiaomi 智能音箱 Pro | OH2P |
| 小爱音箱 Pro | LX06 |
| 小米 AI 音箱 | S12 / S12A |
| 小米 AI 音箱（第二代） | L15A |
| 小爱音箱 Play（2019） | LX05 |
| 小爱音箱 万能遥控版 | LX5A / LX05A（红外版） |
| 小爱智能家庭屏 10 | X10A |
| Xiaomi Sound Pro | L17A |

### 🚗 正常支持（TTS 问答链路完整；连续对话不支持，关 streamResponse）

| 名称 | 型号 | 备注 |
|---|---|---|
| **小爱音箱 / Redmi 小爱音箱（Wi-Fi）** | **L06A** | 零售编号常见 **L607 / L607A**，协议型号 L06A |
| 小爱音箱 mini | LX01 | |
| 小爱音箱 Play | L05B | |
| 小爱音箱 Play 增强版 | L05C / L07A | L05C 播放状态查询异常 |
| 小爱音箱 Art | L09A | 指令 `3-1` / `3-2` |
| 小爱触屏音箱 | LX04 | |
| Xiaomi 智能家庭屏 Mini | X4B / ASX4B | |
| Xiaomi 智能家庭屏 6 | X6A | |
| Redmi 小爱触屏音箱 Pro 8 英寸 | X08E | |
| Xiaomi 智能家庭屏 Pro 8 | X8F | |

### ❌ 不支持

| 名称 | 型号 | 原因 |
|---|---|---|
| 小米小爱音箱 HD | SM4 | 云协议层不开放 |
| 小米小爱蓝牙音箱随身版 | — | 纯蓝牙，无小米云小爱协议，不在米家设备列表 |

### 你的音箱查不到？

1. 跑 `python main.py --list-devices`，看报出的 hardware 字段
2. 对号入座：✅/🚗 直接用；❌ 换一台
3. 表里没有但 TTS 播报正常 → 大概率可用，把 `--list-devices` 输出 + 你的 TTS 指令（[miot-spec.com](https://home.miot-spec.com/) 查 `siid-aiid`）提个 PR 补进指令表

---

## 🚀 升级方式

```bash
git pull
cp .env.example .env && nano .env   # 填 MI_USER / MI_PASS / DEEPSEEK_API_KEY（或 OLLAMA_BASE_URL）

python main.py --list-devices       # 1. 先自检你的音箱档位
python main.py                      # 2. 跑起来（L06A 记得 llm.stream: false）

docker compose up -d --build        # 或容器部署
```

**L06A（L607A）用户特别提示**：在"正常支持"档，TTS 问答链路完整，但请保持 `llm.stream: false`（连续对话实验功能在你这档不好用，关了更稳）。