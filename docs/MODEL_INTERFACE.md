# 模型联调接口

日常使用以 `tools/campaign.py` 为入口，见[快速上手](QUICKSTART.md)。它会串联准备、节目外交、执行、独立复盘和素材导出，本文保留底层阶段接口用于定位问题。共同规则、人设和攻略示例在 `configs/prompts/`。

五国节目模式每轮包括 5 次事前意图、10 次公开发言、5 次私信、0–5 次回信、5 次密封决定和1次裁判；再加5次本国复盘及1次观众解说，最多37次调用。`manifest.json` 会汇总服务返回的 token 用量与缺失用量的调用数，不推算账单。所有模型使用独立上下文，观众解说不回流玩家。

各国、裁判、解说可以分别配置模型文件，部分覆盖项会与默认模型合并。兼容端点可以设置 `include_thinking_parameter`、`json_mode` 和受限 `request_parameters`；默认仅对硅基流动发送顶层 `enable_thinking`。新参数进入缓存指纹，不能恢复时悄悄换生成设置。提供通用配置不代表已对每个供应商完成真实验收。

默认联调由 `tools/model_client.py` 调用硅基流动 Chat Completions，默认模型为 `deepseek-ai/DeepSeek-V4.1-Flash`。参数见 `configs/model.example.json`：JSON object 输出、关闭思考模式、temperature 0.3、单次最多 1800 输出 token。接口字段依据[硅基流动官方文档](https://docs.siliconflow.cn/docs/api/chat-completions-post)。

密钥仅从模型配置的 `api_key_env` 环境变量读取，默认名称为 `IRON_SILICONFLOW_API_KEY`。配置文件只记录环境变量名，不保存密钥。调用记录包含模型名称、响应 ID、trace ID、用量、耗时、真实 UTC 起止时间、提示词和响应，不包含认证请求头。旧缓存没有起止时间时，离线验收标为未知，不用文件修改时间代替。

## 单回合试验

`tools/model_trial.py` 按以下阶段运行：

1. 在同一暂停时刻保存并解析五国状态，逐国读取科研界面，确认可见空槽编号；关闭提示框后采集。科技、国策和装备名称来自当前安装的游戏与 Mod。
2. 五个独立模型上下文分别生成公开发言。每轮收齐后统一公开；第二轮可回应前一轮发言，不能提前看到同轮其他国家的回复。
3. 可选择公开沟通后每国发送一封定向私信或弃权。消息只进入发送者和接收者的决策上下文。录制模式另给实际收件人一次回信机会，再分别递交密封决定。底层试验默认每国最多一个动作，录制对局按 `action_limit` 配置 1–6 个；均可不行动。
4. 程序校验接口、国家、回合、观察 ID、可见目标和动作数量，再交给独立裁判上下文审查。
5. 裁判逐国返回请求 SHA-256 与批准结果。只有五份原请求均获批准，才导出供执行器读取的请求文件。裁判不会改写玩家动作。

模型返回的 `identity` 包装可以无损展开为执行接口的顶层身份字段。原始响应与规范化请求分别保留；动作和理由保持原样。带有冲突身份字段的响应会被拒绝。失败阶段可以使用 `--resume` 复用提示词与模型都一致的已有响应，避免重新抽样改变已经提交的决策。

不传节目配置时，底层默认保留一轮广播。使用 `--communication-rounds 2 --private-diplomacy` 启用两轮公开讨论和一轮定向私信，五国每回合共 21 次模型请求。`campaign.py` 传入节目配置后，另启用事前私密意图、来信回复、分国人设与模型；相关产物位于 `private_intents/`、`private_replies/`、`private_diplomacy/` 和 `commitments.json`。承诺只证明曾经说过，履约状态不能由原话自动推定。

连续回合传入 `--previous-trial` 和 `--previous-turn`，程序核对上一回合已完成、当前存档日期及五国状态一致后，为各国注入最近两回合的本国决定、执行结果、公开消息、本人参与的私信、意图、复盘和本国裁判反馈。`campaign.py` 另外维护各国独立经验与可见承诺库。未开放的动作、未知容量均明确标为未知；裁判可以指出理由中的事实问题，但不改写合法策略。

## 运行方式

在本机终端设置好密钥环境变量后，为新实验指定唯一名称。下面的 `MY_NEW_TRIAL`、`MY_NEW_TURN` 与 `CURRENT_PID` 需要替换为本次实验名和已确认 AI 停用的实际游戏进程 ID：

```powershell
.venv/Scripts/python.exe tools/model_trial.py prepare --name MY_NEW_TRIAL --known-disabled-pid CURRENT_PID
.venv/Scripts/python.exe tools/model_trial.py decide --name MY_NEW_TRIAL --communication-rounds 2 --private-diplomacy
```

模型批准结果位于 `runs/MY_NEW_TRIAL/decision_result.json`；公开发言、私密决策和原始调用分开保存。确认该结果为 `approved` 后，调用现有执行器：

```powershell
.venv/Scripts/python.exe tools/run_turn.py `
  --observations runs/MY_NEW_TRIAL/observations --requests runs/MY_NEW_TRIAL/requests `
  --name MY_NEW_TURN --known-disabled-pid CURRENT_PID `
  --config configs/match.fifteen_days.json `
  --decision-source siliconflow:deepseek-ai/DeepSeek-V4.1-Flash:referee-approved
```

`configs/match.fifteen_days.json` 将一回合定义为 360 游戏小时。执行器每 24 小时暂停并检查一次真实日期，全部玩家仍只在战略回合开始时提交一次决策。所有动作执行和存档验证完成后才推进时间。遇到未验证的动作或时钟偏差会停在当前阶段。

下一回合准备示例：

```powershell
.venv/Scripts/python.exe tools/model_trial.py prepare --name NEXT_TRIAL --previous-trial MY_NEW_TRIAL --previous-turn MY_NEW_TURN --known-disabled-pid CURRENT_PID
```

普通科研/国策完成提示可关闭。世界新闻仅在有效游戏脚本确认 `major=yes`、识别到唯一匹配的可见选项、该选项没有游戏效果时自动确认，并保留截图及来源哈希。带效果的事件不自动选第一项。来源已核实的全球新闻可进入下回合公共历史。

若动作已全部处理、推进时钟中断，使用 `tools/recover_clock.py --name MY_NEW_TURN --known-disabled-pid CURRENT_PID`。它读取当前真实日期和新存档，核对原命令仍在执行或正常完成，只推进剩余小时。它不会重新提交动作；不确定的动作必须先通过原执行阶段恢复机制核实。

动作只开放本轮已校准菜单中的目标，包括可见空科研槽、候选国策、军工生产线、指定建造地区，以及完成本机验收后的绑定军团移动、停止和普通攻击。完整参数与部分响应语义见[动作合同](ACTION_CONTRACT.md)和[陆军控制](ARMY_CONTROL.md)。`action_options` 描述执行能力；没有提供某项操作，不代表游戏中已经没有建筑位、队列已满或不存在其他战略选择。

录制版本具备连续对局、独立观众点评、私有经验、素材导出和固定终点计分，见[场景与计分规则](SCENARIOS.md)及[录制版本报告](experiments/RELEASE_20261108.md)。当前观察仍标记为 `public_debug`：已提供本国军队与参与战斗的记录，完整战争迷雾、条约执行及原版战争胜负条件仍未全部接入；省份计分不等于原版投降判定。
