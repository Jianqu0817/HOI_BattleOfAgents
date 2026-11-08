# IRON 原生 API 五国对局交接（2026-11-08）

## 停止指令与实际状态

用户最新指令：**“停止吧，记录当前工作，我会由其他 Agent 去完成。”**

本轮已停止开发和测试；不要因阅读此文自动续跑。用户此前要求测试 50 回合，并启用导演、裁判。实际完成 **16/50 回合**，第 17 回合在决策阶段因 SiliconFlow HTTP 402（余额不足）中断。用户选择“换用其他接口，并记录模型变更”，但停止前尚未提供新接口地址、模型名和密钥环境变量名。

- 已检查没有运行中的 `native_campaign`、`native_turn` 或 `verify_native_campaign` Python 进程。游戏 `hoi4.exe` PID **43004** 保留打开并暂停；没有为交接关闭游戏或其他服务。
- 待启动的 `/root/native_observations` 子代理已中断，其余相关审查子代理已完成。
- 第 17 回合已有聊天、意图、私聊和回复缓存；尚无已完成的密封决策文件，也没有 `native_turns/turn_017` 执行目录，**未执行第 17 回合命令**。
- 未提交、未推送当前测试修复。冻结的运行 manifest 仍为 `needs_inspection`，保留真实 402 原因；用户主动停止另记于本交接，不伪造完成状态。
- 密钥没有写入本交接；会话临时 key store 已清空。后续只使用密钥环境变量，不复述历史对话里的明文密钥。

## 本地入口与证据

项目根目录：`D:/DS_workspace/IRON`。

| 用途 | 路径（相对于根目录） |
| --- | --- |
| 本次完整运行目录 | `runs/campaigns/native_show_50_v2/` |
| 冻结运行配置、回合状态 | `runs/campaigns/native_show_50_v2/manifest.json` |
| 启动时本地配置（Git 忽略） | `configs/campaign.native50.local.json` |
| 暂停时状态快照 | `runs/campaigns/native_show_50_v2/handoff_state.json` |
| 余额中断、调用用量汇总 | `runs/campaigns/native_show_50_v2/balance_stop_summary.json` |
| 审计报告 | `runs/campaigns/native_show_50_v2/verification.json` |
| 模型调用缓存 | `runs/campaigns/native_show_50_v2/trials/turn_001` 至 `turn_017` |
| 决策及流程文件 | `runs/campaigns/native_show_50_v2/turns/` |
| 原生命令与执行回执 | `runs/campaigns/native_show_50_v2/native_turns/` |
| 观众素材索引 | `runs/campaigns/native_show_50_v2/index.html` |
| 已生成的 16 回合素材包 | `runs/campaigns/native_show_50_v2/episodes/turn_001` 至 `turn_016` |

`runs/` 和本地配置被 Git 忽略；仅克隆 GitHub 仓库不会获得这些实局证据。转移到其他机器时需另行保留运行目录、必要存档和本地运行依赖。

## 游戏与 API 现场

- HOI4 **1.19.4.2**；固定构建 `6ae9f874_35ea878`。
- EXE：`F:/SteamLibrary/steamapps/common/Hearts of Iron IV/hoi4.exe`。
- EXE SHA256：`bccc62909b6a1ec2c1d5a46ef18608e4d0c7cdc89ad341b7402a23c40f7a8037`。
- PID **43004**；进程创建标识 **134386021022986361**；命名管道 `\\.\pipe\iron-native-43004`。
- 控制国家 AUS（country_index/played_index **11**），暂停；`ai_enabled=false`、`global_ai=true`、`ai_override=true`。最后一次较全面检查中五个参赛国的原生 AI 均被禁用。
- 最后状态 `date_raw=60764991`，原生显示 `1936-08-23T15:00:00`，存档日期口径 **1936.8.23.16**；时钟 `remaining_hours=0`。
- 起始 raw **60764607**，存档日期 **1936.8.7.16**；16 个完整回合共推进 **384 游戏小时 / 16 天**。
- 存档目录：`C:/Users/23668.JIANQU/Documents/Paradox Interactive/Hearts of Iron IV/save games`；准确存档文件及哈希看各回合结果和审计报告。
- core、clock/control、diplomacy v2、air/navy v2 及下面的事件 sidecar 已加载。PID、地址、加载 DLL 和回执均与当前进程生命周期有关，重开游戏后不能直接套用。

这次测试通过原生 API 执行，未回退鼠标图像指令。已完成 **80 份密封国家决策、159 条动作**：132 条 `division.move`、27 条 `air.mission`。记录到 37 次抽样移动首次抵达，不代表全部 132 条均抵达。此次运行没有新宣战或海军任务，不能把既有模块支持等同于本次已实测。

## 模型、节目流程与费用证据

- 五国：**GER、FRA、ITA、AUS、SWI**；每国 action_limit=2，communication_rounds=2。
- 旧模型：`deepseek-ai/DeepSeek-V4.1-Flash`；旧地址 `https://api.siliconflow.cn/v1`；密钥变量 `IRON_SILICONFLOW_API_KEY`。thinking=false、temperature=0.3。
- 各国、裁判、公开主持、轮换私密采访和观众导演点评使用同一模型、各自独立上下文。
- `strict_public_context`、角色表演、游戏时间承诺、模型点评均开启。导演的点评/高光由模型生成，`editing_plan.json` 是规则生成的剪辑草案，不能称为模型自主完成剪辑。
- 配置自适应步长：和平 168h、战争 24h、关键 6h，时钟每 6h 检查；此次战争场景实际完整回合各为 24h。
- 素材包含主持、采访、导演点评、字幕 SRT、证据 CSV/JSONL、voice_lines、角色信息、cast.html、editing_plan。**观众素材含私密信息，绝不作为参赛者输入。**
- 本次没有合成 TTS；角色参考音频仍未准备。

已成功缓存的 16 回合加第 17 回合部分调用：**536 次、6,731,763 tokens**（输入 6,603,849，输出 127,914）；另有 readiness 1 次、19 tokens。合计已知记录 **537 次、6,731,782 tokens**。失败 402 请求没有 usage 记录；此处不是精确账单或人民币费用。单次最大输入 22,080 tokens。

完整 16 回合自身为 513 次、6,396,499 tokens。阶段累计：intent 85、diplomacy 170、private 85、reply 45、decision 80、referee 16、public_host 16、audience_interview 16、director_commentary 16、communication_annotation_repair 7。

时间记录：manifest 开始时间北京时间 **2026-11-08 21:16:09**，第 17 回合开始 **22:25:40**，两者相差约 **69 分 31 秒**，包含流程处理和中断恢复，不能当成纯模型推理时间；未另行记录精确主动工作总时长。

## 本轮本地修复与实测

### 语言与信息隔离

1. 导演引文末尾额外句号导致严格校验失败：`episode.normalize_commentary_quotes` 仅在去掉一个末尾句号后仍为原文精确子串时修正；不能核实的事实标注省略，原始模型响应保留，并附 normalization 回执，审计重新计算。
2. 公开主持递归引用旧主持消息：`story_channels.py` 不向主持传入非空旧主持消息；`performance.py` 限定主持直接引用公开原话/世界事实。
3. 第 7 回合把收到的他国承诺标成自己承诺：新 `communication_annotations.py` 在严格模式投影结构化标注，保留原话和收件人，不增加已用尽的付费修复次数；旧修复缓存仍复用。
4. 第 13 回合模型仍说“维持至第 12 回合”：保留真实的模型话语退化，只剔除已过期的结构化承诺期限，不替模型延长承诺或改写发言。
5. 第 17 回合模型回复了非本轮来信者：`project_reply_routes` 只投递每个实际来信者的首条原始回复；其他草稿保留 `delivery_projection` 未送达回执。只有发送者自己的决策上下文收到自己的投递回执；观众可看 `undelivered_private`，不能假装目标收到。

后续可考虑专门的“收信后主动外联阶段”满足角色转述/挑事需求；本轮没有扩展协议，不应绕过现有回复收件范围。

### news.66 弹窗：已限定验证，非通用事件能力

第 9 回合 24h 时钟推进到一半出现柏林奥运会 `news.66`。原生流程安全暂停，余下 12h。修正了窗口字段误读：**window+0x60 是 pending record ID，不是国家索引**；原事件 BEL，展示作用域 AUS。脚本确认两选项无效果，新闻到达时 immediate 全局旗标已执行；AUS 可选 option0，GER 专用 option1。

- 新 `native/iron_bridge/event_sidecar.cpp`、`event_close_sidecar.cpp`、`event_validation.hpp`、`event_test.cpp`，以及两份 build-events PowerShell 脚本。
- `native_events.py` 识别 pending records 与已解决但仍显示的窗口，隐藏窗口而事件未解决仍算 pending。
- v1 sidecar：`runs/native-events-v1/iron_events_v1.dll`，SHA256 `9c5c19bfa636ef633bddaaa9b4428e94d6a33ff29044a9b414b284e7a1f61b48`，op `native_events_v1`。实测 probe 合法，再通过正常 SelectEventOption 队列单次提交 option0，pending record 消失。
- v2 sidecar：`runs/native-events-v2/iron_events_v2.dll`，SHA256 `ede131a5ce91fefc5338e4dfb912171c0a50ed190b6d3aa8a813669780750784`，op `native_events_v2`。仅在 pending ID 和事件指针均不在 pending records 后，走正常窗口生命周期关闭；不再次提交选择。实测后 pending/resolved_windows 均空，日期未变。
- `NativeGame.call` 的显式 request ID 与 durable journal 用于真实提交；最终 Python typed wrappers 已改用同一机制并通过定向测试，真实接受证据来自 root 的显式原生调用。
- **旧窗口地址现已释放，不得重用；已加载 DLL 不要覆盖。一般事件仍不支持，只有 news.66 完成资格验证。**新闻正文尚未自动接入参赛者公开事件板。

证据：`runs/native50_preflight_20261108/news66-*.json` 及 `requests/`；逆向与接受记录 `runs/api_bridge/EVENTS_ABI.md`。

### 中断后仅恢复时钟

`NativeTurn.resume_interrupted()` 与 CLI `--resume-clock` 新增，要求完全相同计划、进程身份、日期、控制国、AI 状态和已知命令回执，且 pending/resolved 窗口为空、原生 remaining_hours=0；未知回执拒绝恢复。保持原 runID/sequence，只追加 `resume_reconciled` 并推进余下时钟，不重发军令。

第 9 回合实测只续 12h 至 raw **60764823**，合计 24h，计划哈希不变，存档校验成功；旧中断结果保留于 `native_turns/turn_009/result.interrupted.0001.json`，最终 result 为 completed，runID `258187ce15104e67`。

`NativeCampaign --resume` 不会自动调用这个中断恢复；它正常复用 completed 结果。未来事件中断需先核实现场，再显式恢复 NativeTurn，之后续 campaign。任意中途崩溃、remaining_hours>0 的情形没有获得通用自动恢复资格。

## 验证结论与边界

最近命令：

```powershell
.venv/Scripts/python.exe tools/verify_native_campaign.py --name native_show_50_v2 --expected-turns 50
```

结果 **incomplete：12,449 项通过、0 失败、4 项未知**；真实存档字节/SHA 校验通过。未知项：campaign_committed_completion、turn_17:committed_completion、all_sealed_decisions、no_unreferenced_or_replayed_native_orders。50 回合未完成，这些不能标绿。

修复了部分审计与正在执行下一回合的竞态：部分模式只验证 committed prefix；最终完整审计仍严格比对全部 journal 与执行目录。另修复验证器 compact 上下文时的原对象修改及 public_commitment_memory 别名问题。

最后一组定向测试 **50 项通过**：test_native_events、test_native_resume、test_native_turn、test_communication_annotations、test_commentary_quotes、test_verify_native_campaign；事件 C++ 构建和 guard 测试通过；旧五回合 `native_five_show_v1` 仍通过 3,343 项审计。未跑当前改动的最终发布全套检查，未获得当前改动 GitHub CI 结果。

## Git 与后续接手顺序

当前 HEAD：**b9cf8518847a3316298c61664ff276962fed127b**（已发布角色图 v2）。本轮 50 回合测试修复均未提交；工作区还含更早的未发布 UI/外交/海空等改动，不要一键全部暂存或清空。

本轮文件主要是：`.github/workflows/tests.yml`；`tools/episode.py`、`native_campaign.py`、`native_events.py`、`native_turn.py`、`performance.py`、`story_channels.py`、`verify_native_campaign.py`；`test_native_events.py`、`test_verify_native_campaign.py`；新增 `communication_annotations.py`、`test_commentary_quotes.py`、`test_communication_annotations.py`、`test_native_resume.py`；上面的 6 个事件 C++/构建文件；以及本交接。

`tools/model_trial.py` **混有旧 UI 改动与本轮改动**：旧内容涉及 `check_observation_checkpoint`、`check_partial_observation`、`prepare`；本轮涉及 `call_country`、`decide`、新 `decision_private_context`。`showrunner.py`、`test_episode.py` 等还有旧修改，不能当成本轮全部拥有。选择性暂存时，不要删掉 zero-context diff 的前面 hunks 后直接 git apply，后面插入位置会错；应基于 HEAD 构造所需函数变更并检查 index 内容。

下一位 Agent 在获得用户继续授权后：

1. 接收新 API 地址、模型名、密钥环境变量名，记录模型/provider 变更。不要把密钥放进配置、交接或提交。
2. 先复用当前冻结 manifest、缓存和完成回执。注意 campaign 的 `--resume` 使用 **manifest.config**，不是随便改外部本地配置就会切换接口。应实现明确可审计的模型切换记录/续跑方式，并调整相应验证；不得改写旧调用签名伪称新模型生成了旧缓存。
3. 核实游戏仍在同一进程和末次日期，保留第 17 回合未执行的事实；不要重跑前 16 回合、重发军令或创建新 request ID 猜测未知副作用。
4. 接续第 17 回合尚缺的阶段；多数通信缓存可复用。若游戏被重启，先重新核实/加载对应存档及桥接，不能沿用旧进程地址。
5. 实际完成 50 回合后再做完整审计、相关发布检查和选择性 GitHub 同步；如继续失败，按 efficient-development 的停止条件保留已完成结果。

## 仍需后续处理的项目

- 本轮 50 回合未完成；替代接口未配置；当前修复未发布。
- 新闻正文自动交付、一般事件处理、更多私聊主动外联尚未完成。
- 旧角色卡仍由用户后续撰写；不要自行定稿人设。六角色各 5 张共 30 张 v2 PNG 已发布，入口 `assets/characters/index.html`，注册表 `configs/characters.example.json`；v1 归档在 `runs/character_art_revision_v2/original_assets`。
- GPT-SoVITS 已部署 `D:/GPT-SoVITS_IRON`，CPU v2，服务地址端口 9880；服务未因这次停止而关闭。六角色缺参考音频/声线素材，尚无实际角色 TTS 合成验证。
- 坚持公开/私人/观众通道隔离：可公开可观察结果和角色主动说辞；执行完成不使私密命令自动公开；导演可采访挑事，不能泄密或干涉战局。角色可质问、可转述，但只能依据自己可见信息。

后续遵循用户级 `C:/Users/23668.JIANQU/.codex/skills/efficient-development/SKILL.md`：复用有效成果、最小闭环与改动、按风险验证、无新证据不追加高成本重试。简单子任务 Luna，确认复杂后 Sol，计算机使用 Terra，并控制上下文。本交接不构成继续执行授权。
