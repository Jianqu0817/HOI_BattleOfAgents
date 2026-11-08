# IRON 原生 API 五国对局交接（2026-11-09）

## 停止指令与实际状态

用户指令：**"停止吧。"**

已停止全部开发与测试进程。用户此前授权接手前任 AI 的 50 回合测试并用 kuaipao.ai 新接口恢复。实际完成 **29/50 回合**，第 30 回合在**私信阶段**被停止。

- 已确认对局 Python 进程随任务停止终止；残留的两个 python 进程是 GPT-SoVITS TTS 服务（端口 9880），按惯例保留。
- 游戏 `hoi4.exe` PID **43004** 存活（creation_time 134386021022986361 与 manifest 一致）；**paused=true、remaining_hours=0**；控制国 AUS（index 11），`ai_enabled=false`、`global_ai=true`、`ai_override=true`。
- 最后日期 raw **60765285**（原生显示 1936-09-04T21:00，存档口径 **1936.9.4.22**）。注意第 25 回合起回合结束时刻从 `.16` 变为 `.22`，为既有 24h 预算累加，验证器逐回合核对通过。
- manifest 状态 **`ready_for_next_turn`**、error 已清除（第 29 回合正常完成后才停止，非崩溃中断）。
- `native_turns/turn_030` 不存在：**第 30 回合未执行任何游戏命令**，无需对账恢复。
- 第 30 回合已缓存 19 次调用（intent×5、diplomacy×10、private×4）；AUS 的 private 调用停止时在飞行中未落盘，恢复时会重调。
- 累计用量（manifest.usage）：**927 次调用、13,532,730 tokens**（输入 12,990,752 / 输出 541,978），calls_without_usage=0。

## 恢复命令（获得用户授权后）

```powershell
$env:IRON_KUAIPAO_API_KEY = <从密钥来源注入；明文只存 gitignored 的 runs/secrets/kuaipao_api_key.txt>
.venv/Scripts/python.exe tools/native_campaign.py --name native_show_50_v2 --pid 43004 `
  --save-dir "C:/Users/23668.JIANQU/Documents/Paradox Interactive/Hearts of Iron IV/save games" `
  --resume --turns 50 --switch-model configs/model.kuaipao.reasoning.json
```

`--switch-model` 会幂等复用 manifest `model_switches` 里已记录的第二条台账（reasoning 预算调整），不会重复记账；若省略该参数则自动按已记录台账折算生效配置。密钥**不得**写入配置、交接或提交。

## 本次接手完成的机制工作（均已发布在工作区，未提交 Git）

### 1. 可审计模型切换台账（manifest `model_switches`）

- 第一条（applied_at 2026-11-08T15:25:11Z，first_affected_turn=17）：SiliconFlow → kuaipao.ai（`configs/model.kuaipao.json`）。turn_017 的 23 次旧供应商缓存经 trial 内 `model_switch.json` 桥接复用，未重付。
- 第二条（first_affected_turn=18，`configs/model.kuaipao.reasoning.json`）：同供应商追加 `request_parameters={reasoning_effort:low, max_tokens:4000}` + timeout 240s。原因：kuaipao 的 DeepSeek-V4.1-Flash 带 reasoning，旧 phase 上限 700/900 会被推理耗尽导致 `finish_reason=length`。
- 冻结 config 与 `config_sha256` 未动；验证器按调用 started_at 在时间线上选有效配置逐条审计（`model_switch_ledger` 检查通过）。

### 2. 供应商方差韧性（全部"投影+回执"先例，原始响应永不改写）

| 问题 | 处理 | 回执字段 |
| --- | --- | --- |
| 偶发非法 JSON / 空响应 / 截断 / 5xx、429 | `model_client.complete` 有界重采样（≤3 次；HTTP 4xx 永不重试） | — |
| 聊天响应携带 `actions`（原硬失败） | 剥离进契约字段，原始留档 | `chat_actions_projection` |
| 私信承诺对象≠收件人 | 并入 `project_commitment_quotes` 投影 | `annotation_projection.changes[kind=omit_annotation_misaddressed_recipient]` |
| 决策格式混入私信/外交、超长消息 | 投影为显式 no-op 消息（"本轮未发言"） | `speech_projection` |
| 主持人引用不存在来源 | 剔除无效行，保留合法行 | `host_projection` |
| 采访多余字段 | 保留契约两字段 | `interview_projection` |
| 导演点评单类超 8 条 | 截前 8 条 | `normalize_commentary_quotes` 的 `cap_section` |
| 裁判抄错 request_sha256（approved=true） | 客户端恢复提示词原值 | `sha_projection`（referee 记录 normalized_response） |

验证器（verify_native_campaign）对以上每类回执都能从原始响应重算并强校验。密封决策链（validate_trial_request→裁判→verify_decisions→原生执行）语义未放松。

### 3. 测试

全套离线测试 **305 项通过**（新增 `tools/test_model_switch.py` 14 项 + 各投影测试）。每处修复都先测后跑。

## 已验证的运行结果

- 第 17 回合跨供应商恢复后做了部分审计：**13,235 项通过、0 失败**（3 项 unknown 为"未满 50 回合"占位）。
- 第 17–29 回合全部完成并通过逐回合校验（时钟精确累加、存档哈希、密封请求不变、动作回执）。战况：AUS-ITA 战争持续，回合长度固定 24h。
- 期间未发生任何"回执≠生效"类游戏执行异常；两回合出现 reasoning 长尾导致单回合 300s 量级延迟，属正常。

## 后续接手要点

1. 恢复后直接续 30→50，无需任何额外机制；完成 50 回合后跑最终完整审计：
   `.venv/Scripts/python.exe tools/verify_native_campaign.py --name native_show_50_v2 --expected-turns 50`
2. 预计仍会遇到新的模型纪律类硬校验失败（每类需按"投影+回执+验证器重算+单测"扩展）；运行中偶发失败重跑 `--resume` 即可，完成回合永不重跑、命令永不重发。
3. 未提交的 Git 改动含本轮全部机制修复与更早的未发布改动，不要一键暂存（见前任交接 2026-11-08 的选择性暂存警告）。
4. 观众素材含全部秘密，不得作为参赛者输入；TTS 参考音频仍缺。
