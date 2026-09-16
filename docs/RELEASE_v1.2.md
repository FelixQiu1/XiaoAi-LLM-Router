# XiaoAi-LLM-Router · v1.2 更新说明

> 用法：把本文档贴成 GitHub 的 Release Notes（v1.2），或拆成两个文件：
> 上半部分 → RELEASE_v1.2.md，下半部分"支持列表" → docs/SUPPORTED_DEVICES.md。

---

## 一、v1.2 更新内容（对照 v1.1 的差异）

### 1. 收音源重做：从"MiService MQTT"改为"模拟提问 + 对话记录轮询"（机型无关）

v1.1 押注 MiService 的 MQTT Bridge，但它是 MiService 的可选插件——不同版本 topic 不同、默认不开启，部署门槛高。v1.2 采用整个生态（xiaogpt / mi-gpt）验证过的通用方案：

```
小爱音箱 ──真实提问──▶ 小米云 ASR ──▶ 小爱原生应答
      ▲                        │
      │                        ▼
  TTS 播报  ◀── 本网关拦截 conversation 记录 ◀── 轮询 userprofile.mina.mi.com
      │                        ▲
      └── 用户命中唤醒词("问问 AI")时，LLM 答案经 MiNA TTS 指令(5-1 / 7-3 等)注入
```

- 新增 `mi_conversation.py`（模块 1b）：用小米账号 serviceToken 轮询 `device_profile/v2/conversation`（limit=2，与 xiaogpt 同款），以"最后一条 query 的 requestId"做水位线，重启不重放
- `miservice_glue.py` 降级为可选 fallback（`ingest: poll` 模式），默认 `ingest: conversation`
- 收益：**机型无关**——任何在米家 App 里能问"小爱同学"的 Wi-Fi 音箱都直接支持，不再依赖 MiService 插件是否开启

### 2. 机型指令表内置（`mi_conversation.py` 的 `HARDWARE_COMMAND_DICT`）

TTS / 唤醒指令按机型自动适配（来源：mi-gpt 官方兼容表 + xiaogpt config.py，两者互相印证）：

| 档位 | 型号 | 连续对话 |
|---|---|---|
| 完美 | OH2P / OH2 / LX06 / S12A / L15A / LX5A / LX05 / X10A / L17A | 支持（建议关） |
| 正常 | **L06A** / LX01 / L05B / L05C / L09A / LX04 / X4B / X6A / X08E / X8F / L07A | 不支持（关 stream） |
| 不支持 | SM4（小爱音箱 HD）/ 纯蓝牙随身版 | — |

自动检测：启动时用 `device_profile` 返回的 `hardware` 字段查表；查不到的机型走默认 `5-1` 并打 warning，提示用户把指令表贡献回仓库（一行配置）。

### 3. 依赖与部署

- `requirements.txt` 增加 `miservice`（PyPI 上的官方 Python 库——v1.0 误以为它不存在，实际 `pip3 install miservice` 可直接装）
- Docker 不变（v1.1 的 Dockerfile 已解决启动慢 / 模块缺失）
- `--demo` 模式补上 L06A 示例：`python main.py --demo --hardware L06A`

### 4. 其他

- 新增 `docs/SUPPORTED_DEVICES.md`（本文件第二部分，可独立成文件）
- 新增 `--list-devices` 子命令：打印当前小米账号下所有小爱音箱 + 支持档位，让人**在部署前**就知道自己的音箱支不支持
- 测试补 2 个：机型指令表查询 / 未知机型 fallback

---

## 二、小爱音箱支持列表（可独立成 docs/SUPPORTED_DEVICES.md）

> **判断口诀：在米家 App 里能看到、能语音问"小爱同学"的 Wi-Fi 音箱 = 支持；纯蓝牙随身版（不在米家设备列表里）= 不支持。**

### 完美支持（TTS 问答 + 实验性连续对话）

| 名称 | 型号 | 备注 |
|---|---|---|
| Xiaomi 智能音箱 | OH2 | |
| Xiaomi 智能音箱 Pro | OH2P | |
| 小爱音箱 Pro | LX06 | |
| 小米 AI 音箱 | S12 / S12A | |
| 小米 AI 音箱（第二代） | L15A | |
| 小爱音箱 Play（2019） | LX05 | |
| 小爱音箱 万能遥控版 | LX5A / LX05A（红外版） | |
| 小爱智能家庭屏 10 | X10A | |
| Xiaomi Sound Pro | L17A | |

### 正常支持（TTS 问答链路完整；连续对话不支持，请关 streamResponse）

| 名称 | 型号 | 备注 |
|---|---|---|
| **小爱音箱 / Redmi 小爱音箱（Wi-Fi）** | **L06A** | 零售编号常见 L607 / L607A，协议型号 L06A |
| 小爱音箱 mini | LX01 | |
| 小爱音箱 Play | L05B | |
| 小爱音箱 Play 增强版 | L05C / L07A | L05C 播放状态查询异常 |
| 小爱音箱 Art | L09A | 指令表为 `3-1` / `3-2` |
| 小爱触屏音箱 | LX04 | |
| Xiaomi 智能家庭屏 Mini | X4B / ASX4B | |
| Xiaomi 智能家庭屏 6 | X6A | |
| Redmi 小爱触屏音箱 Pro 8 英寸 | X08E | |
| Xiaomi 智能家庭屏 Pro 8 | X8F | |

### 不支持

| 名称 | 型号 | 原因 |
|---|---|---|
| 小米小爱音箱 HD | SM4 | 云协议层不开放 |
| 小米小爱蓝牙音箱随身版 | — | 纯蓝牙，无小米云小爱协议，不在米家设备列表 |

### 你的音箱查不到？

1. 先跑 `python main.py --list-devices`：它列出你账号下所有小爱设备及其 hardware 字段
2. 对号入座：在 完美/正常 表里 → 直接用；在 不支持 表里 → 换一台
3. 表里没有但 TTS 播报正常 → 大概率可用，把 `--list-devices` 输出和你的 TTS 指令（miot-spec 查 `siid-aiid`）提个 PR 补进指令表，下一档机型就"完美支持"了

---

## 三、给用户的 3 行快速判断

```
你的音箱在米家 App 设备列表里吗？ ── 否 → 不支持（纯蓝牙随身版）
            │ 是
它说话时你听到的是"小爱同学"音色吗？ ── 否（只有蓝牙配对、无云语音）→ 不支持
            │ 是
查上表：完美/正常 都能用（正常记得关 stream）；不支持 换机型
```