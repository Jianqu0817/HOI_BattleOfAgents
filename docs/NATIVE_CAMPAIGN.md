# 五国模型对局：原生执行

`tools/native_campaign.py` 复用已有 `model_trial` 与 `showrunner`：五国独立私密意图、两轮同步公开讨论、定向私信/回复、密封决策、裁判哈希审核。批准后的原始决策转换为 `NativeTurn`，由游戏主线程提交正常命令，不操作鼠标或键盘。

## 开始与恢复

先按 [原生 API](NATIVE_API.md) 构建并加载核心、clock、control、diplomacy-v2、air-navy-v2。游戏应暂停，当前玩家须包含在参赛国中，参赛国原版 AI 已关闭。只支持已验收的 Windows 游戏版本。

```powershell
python tools/native_campaign.py --name native_show --pid YOUR_PID --save-dir "YOUR_SAVE_DIRECTORY" --prompt-key --provider-check
# 继续未完成对局，复用原配置及已保存模型响应
python tools/native_campaign.py --name native_show --pid YOUR_PID --save-dir "YOUR_SAVE_DIRECTORY" --resume --prompt-key
```

密钥可隐蔽输入，也可预先设置模型配置中的环境变量。Windows 运行入口会读取当前用户持久环境变量，已有进程无需重启桌面应用即可使用新保存的令牌。每国、裁判和解说的模型覆盖配置沿用 `campaign.py` 的配置格式。默认 `configs/campaign.native.example.json` 为 GER、FRA、ITA、AUS、SWI 五国，五回合，每国至多两个动作。

新对局使用事件触发的自适应时间：和平720小时（约30天）、战争24小时、关键事件6小时。720小时是上层共同预算，原生桥仍分成至多6小时的小段推进并检查事件；宣战、控制权变化或待处理事件会提前返回观察/决策，不会无条件跳过整月。已冻结的旧对局保留原来的时间配置。

`show.quiet_interludes=true` 时，若没有新增公开局势事件，第二轮公开互动可用简短的关心、调侃或回怼表现角色关系；仍先回应有意义的提议。没有公开事件不等于没有战斗。这复用原有讨论调用，不增加额外聊天轮次，不向其他国家公开私信/密封命令，也不强制为了节目效果改变策略。

正式录制配置为 `configs/campaign.recording50.json`：50回合，五国分别使用 `model.cast.gpt/claude/gemini/deepseek/qwen.json`，GLM承担公开主持、裁判和赛后导演。各配置只保存环境变量名；连通性结果不能替代完整对局验收。

`--turns 1` 表示先完成整个对局中的第一回合；随后 `--resume` 继续剩余回合，不重做已完成回合。配置、身份和模型请求被冻结。游戏重启或执行记录未确认时停止，不能换新请求 ID 重发命令。

## 观察与能力

原生只读内存采集当前州/省控制权，原生接口读取师、联队、特遣队和参赛国交战关系。所有观察在同一暂停日期冻结；日期、进程身份和构建版本都必须匹配。静态邻接与不可通行地形复用已有地图模块。

候选是完整的 `{id,type,args}`，模型原样选择，软件再次校验归属、观察哈希和参数，裁判审核密封请求哈希，执行前再检查实际状态。仅开放已有证据支持的邻接普通移动、合法宣战和现有区域上的海空军任务；不开放任意不可达区域，不把未采集的经济数据解释为零。

聊天只路由规定字段，附加注释保留在原始响应中，不会执行为动作。逐字承诺引用和最多三条承诺的提取错误，允许一次有具体错误反馈的标注修复，每回合至多两次；发言正文不得改变，期限/对象/条件不得改写，不重新采样游戏决定。

默认示例启用 `compact_native_context`：完整审计记录不变，提示中删除重复承诺的来源路径及历史聊天重复标注，保留原话、承诺条件/期限、当前候选和本国实际结果。已有冻结对局保持原提示格式，恢复时不会因压缩设置重付模型调用费用。

## 结果与视频素材

打开 `runs/campaigns/NAME/index.html`。每回合导出 HTML、SRT、CSV、JSONL、旁白和镜头素材；私密意图是事前摘要，不是隐藏思维链。解说只对观众可见，不进入玩家下一回合上下文。

新建原生对局默认 `audience_timing: "postmatch"`：赛中保留两轮公开讨论、私聊、密封决策、裁判审核和公开主持；观众专属采访及全知导演点评延后生成，不阻塞下一回合。需要原有同步生成行为时可设为 `"inline"`。已有冻结对局未记录该字段时继续沿用原行为，不更改历史配置。

赛后可在没有游戏进程的情况下补齐已完成回合：

```powershell
python tools/native_campaign.py --name native_show --postmatch --prompt-key
# 也可只补齐前五个已完成回合
python tools/native_campaign.py --name native_show --postmatch --turns 5 --prompt-key
```

该入口复用各回合当时的观察、人设、密封决定及已记录的模型切换，不读取之后的局势给采访对象。公开主持原文保留，采访不回写玩家观察、记忆或命令。新增模型调用仍记录实际 token 用量；失败后重跑复用已成功的调用。补齐后的观众素材另存 `episodes_postmatch/turn_NNN/`，原 `episodes/` 导出包保留。此步骤需要可用的模型接口，会产生模型调用费用；不会合成语音或启动剪辑软件。

`manifest.json` 记录每轮开始/结束时间、模型和原生阶段耗时、实际 token 用量。`native_requests/` 与 `native_turns/` 保留意图、回执和效果；移动路径与实际抵达分开标记。完整原文、密封决定、原生存档均留在本机，不上传 GitHub。

```powershell
python tools/verify_native_campaign.py --name native_show
```

离线核验覆盖同步讨论与私信边界、25 份密封请求及裁判哈希、执行计划、原生回执、累计小时、存档哈希和回看素材。实局结果见 [五回合验收](NATIVE_CAMPAIGN_VALIDATION.md)。
