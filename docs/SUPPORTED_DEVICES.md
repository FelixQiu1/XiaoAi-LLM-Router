# 小爱音箱支持列表（SUPPORTED_DEVICES）

> 本表维护 XiaoAi-LLM-Router 对各小爱音箱型号的兼容档位。
> 数据来源：mi-gpt 官方兼容表（idootop/mi-gpt · docs/compatibility.md）+ xiaogpt 机型指令表（config.py 的 HARDWARE_COMMAND_DICT），两者互相印证。
> 最后核对：2025-09。

## 3 行快速判断

```
你的音箱在米家 App 设备列表里吗？ ── 否 → 不支持（纯蓝牙随身版）
            │ 是
它说话时你听到的是"小爱同学"音色吗？ ── 否 → 不支持
            │ 是
查下表：完美/正常 都能用（正常关 stream）；不支持 换机型
```

判断口诀：**在米家 App 里能看到、能语音问"小爱同学"的 Wi-Fi 音箱 = 支持；纯蓝牙随身版（不在米家设备列表里）= 不支持。**

## 完美支持（TTS 问答 + 实验性连续对话）

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

## 正常支持（TTS 问答链路完整；连续对话不支持，请关 streamResponse）

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

## 不支持

| 名称 | 型号 | 原因 |
|---|---|---|
| 小米小爱音箱 HD | SM4 | 云协议层不开放 |
| 小米小爱蓝牙音箱随身版 | — | 纯蓝牙，无小米云小爱协议，不在米家设备列表 |

## 型号速查（部署前自检）

```bash
# 列出你小米账号下所有小爱设备 + hardware 字段 + 档位
python main.py --list-devices
```

## 你的音箱查不到？

1. 跑 `python main.py --list-devices`，看它报出的 hardware 字段
2. 对号入座：完美/正常表里 → 直接用；不支持表里 → 换一台
3. 表里没有但 TTS 播报正常 → 大概率可用，把 `--list-devices` 输出 + 你的 TTS 指令（miot-spec.com 查 `siid-aiid`）提个 PR 补进 `mi_conversation.py` 的 HARDWARE_COMMAND_DICT，下一档机型就"完美支持"了

## 贡献新机型（一行）

```python
# mi_conversation.py 的 HARDWARE_COMMAND_DICT 里加一行：
"LXXX": ("5-1", "5-5"),  # 名称注释
```