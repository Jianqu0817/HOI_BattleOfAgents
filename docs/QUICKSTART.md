# 启动一场可回看的 AI 对局

IRON 在一个 Windows 单人游戏进程中轮流控制 3–5 个国家。所有国家在同一暂停时刻读取本国状态，交流后提交密封决策，再统一执行和推进。完整流程会生成观众回看页、字幕、镜头表和本国复盘经验。

## 1．准备环境

需要 Windows、Python 3.12 或更高版本、Git，以及你自己安装的《钢铁雄心4》。Python 和 Git 应能在终端直接运行。本机已验证的配置是游戏 1.19.4.2、简体中文、1280×720 游戏渲染、单人非铁人模式；Windows 需要可用的简体中文 OCR 和 `en-US` OCR 组件，后者用于严格识别数字军团名与省份 ID。可在 Windows“语言和区域”中添加对应语言及文本识别功能。其他版本、语言或界面比例要重新验证。

将游戏设置为文本存档：在游戏用户目录的 `settings.txt` 中使用 `save_as_binary=no`，重新启动后另存一份测试局。用户目录通常位于 `%USERPROFILE%/Documents/Paradox Interactive/Hearts of Iron IV`，也可能被系统重定向。保留原始玩家存档，使用复制的测试局。

克隆项目并进入目录：

```powershell
git clone https://github.com/Jianqu0817/HOI_BattleOfAgents.git
cd HOI_BattleOfAgents
```

## 2．双击启动

双击根目录 `Start-IRON.cmd`，会打开中文控制台菜单：

| 菜单 | 用途 |
| --- | --- |
| 1 安装／初始化 | 创建 `.venv`、安装依赖、获取固定版本后端、生成本机配置 |
| 2 环境检查 | 报告游戏进程、暂停状态、版本、窗口和密钥变量是否存在 |
| 3 开始对局 | 选择配置、输入唯一对局名称，按需隐蔽输入密钥，然后运行 |
| 4 恢复对局 | 按原对局名称读取冻结配置与执行记录，继续未完成阶段 |
| 5 打开回看 | 打开该对局完成回合的离线观众索引 |
| 6 查看制作指南 | 打开本文和视频制作说明 |

第一次先选 1。安装器会将 HOI4 Live Tools 固定到项目指定版本，并保留已有的 `configs/live.local.json`。它不会替你安装游戏或开启新局。自动发现游戏目录失败时，可在终端明确指定实际路径：

```powershell
python tools/setup.py --game-dir "X:/SteamLibrary/steamapps/common/Hearts of Iron IV" --user-dir "$env:USERPROFILE/Documents/Paradox Interactive/Hearts of Iron IV"
```

将 `X:/SteamLibrary/...` 替换为自己的路径；如果文档目录已重定向，也要替换 `--user-dir`。本机路径配置被 Git 排除，不要把密钥写进去。

## 3．先检查，再跑一个短回合

在游戏中载入准备好的测试局并暂停。保持桌面解锁，允许程序将游戏窗口放到前台。控制期间不要手动操作游戏、鼠标或键盘，不要同时启动其他桌面自动化程序。

菜单选 2，首次可使用 `configs/campaign.quick.example.json`。重点检查输出中的 `game_running=true`、唯一游戏进程和 `pause=paused`。密钥尚未输入时，`model_keys` 可以显示 `false`，开始对局菜单会提示输入。环境检查报告基础状态，不会证明当前地图、分辨率或所有玩法动作已经适配。

菜单选 3，输入同一配置路径，再输入名称，例如 `first_show`。密钥使用不回显的输入方式，只存在启动器及其子进程环境中；退出启动器后不持久保存。配置只写 `api_key_env` 变量名。不要在 OBS 中录制输入凭据的终端。

如果更习惯终端，可从当前 PowerShell 会话读取密钥并运行：

```powershell
.venv/Scripts/python.exe tools/campaign.py doctor --config configs/campaign.quick.example.json
# 在当前会话中配置模型文件指定的环境变量，或改用启动器的隐蔽输入。
.venv/Scripts/python.exe tools/campaign.py run --name first_show --config configs/campaign.quick.example.json --turns 1
```

不要把密钥写入命令示例、配置、版本库或 `setx`。启动器无需你手工执行国家 AI 的切换式控制台命令；它会在暂停时根据实际响应确认各国原生 AI 已停用。`--known-disabled-pid` 仅用于已有同进程停用证据的调试，不是普通安装步骤。

短配置运行 3 轮，每轮 24 游戏小时、每国最多一个动作。`--turns 1` 让本次只新增一个回合，适合先验收本机环境。每个玩法动作都会保存并核验，全部处理完再共同推进；遇到未确认状态会停止并保留记录。

## 4．在哪里看结果

启动器选 5，输入对局名。也可直接打开：

```text
runs/campaigns/first_show/index.html
```

这里链接到每轮的 `episodes/turn_001/index.html` 等离线回看页。页面包含观众可见的各国意图、聊天、命令、执行和结果，可按国家筛选、逐条揭示。它是全知观众素材，不能交给参赛 Agent。

主要记录位置：

| 位置 | 内容 |
| --- | --- |
| `runs/campaigns/<name>/manifest.json` | 冻结配置、阶段、每轮结果与错误 |
| `runs/campaigns/<name>/production.jsonl` | 实际 UTC 阶段场记 |
| `runs/campaigns/<name>/knowledge/<TAG>.json` | 本国赛后经验 |
| `runs/campaigns/<name>/standings.json` | 按本场显式计分规则生成的终点指标／排名 |
| `runs/<name>_t001/` | 观察、模型调用、私密意图、外交、命令、复盘和观众解说 |
| `runs/turns/<name>_t001_exec/` | 逐动作存档核验、时钟与恢复记录 |
| `runs/campaigns/<name>/episodes/turn_001/` | HTML、SRT、时间线、镜头表及截图素材 |

解说或复盘失败不会把已完成的游戏回合重做。程序会记录错误，解说失败时仍可导出事实素材。视频制作步骤见[后期工作流](VIDEO_WORKFLOW.md)。

完成后可以离线核对记录，不操作游戏、不调用模型：

```powershell
.venv/Scripts/python.exe tools/verify_campaign.py --name first_show
```

报告写入该对局的 `verification.json`。它检查冻结配置、身份与私信路由、原请求和裁判哈希、执行记录、共同时间、素材来源及跨回合连续性；缺失证据会明确显示为缺失或未知。结构核对通过不等于策略正确，也不证明解说判断或承诺履约。

## 5．修改模型、人设与预算

复制示例配置为 `configs/campaign.local.json` 再修改；此文件被 Git 排除。所有配置中的相对路径都以项目根目录为准。

| 字段 | 含义 |
| --- | --- |
| `countries` | 3–5 个互不重复、在当前存档中实际存在的国家代码 |
| `step_hours` | 每轮共同推进小时数，范围 1–360；半月为 360 |
| `turn_limit` | 本场最多运行回合数，范围 1–100 |
| `action_limit` | 每国每轮最多动作数，范围 1–6，仍受已验收动作限制 |
| `communication_rounds` | 公开讨论轮数，范围 1–3 |
| `scoring` | 默认 `exhibition` 仅展示；可显式选择省份增量或目标占领规则，见场景说明 |
| `model_config` | 默认玩家模型文件 |
| `player_model_configs` | 按国家覆盖模型文件，例如 `{"GER":"configs/model.ger.local.json"}` |
| `referee_model_config` | 可选裁判模型文件，省略则使用默认模型 |
| `commentary_model_config` | 可选观众解说模型文件，省略则使用默认模型 |
| `model_commentary` | 是否调用模型生成观众解说；关闭后仍可导出事实记录 |
| `summarize_lessons` | 是否调用模型生成本国复盘，默认开启 |
| `show.intents`、`show.private_replies` | 事前意图摘要和私信回复开关 |
| `show.profiles` | 各国名称、人设、初始经验及可选提示词文件 |

模型文件参考 `configs/model.example.json`，填写服务地址、模型名和密钥环境变量名，不能填写密钥值。客户端使用 Chat Completions JSON 对象输出；切换供应商后，需要验证其对当前参数的兼容性。每国可使用同一模型的独立上下文，也可分别配置不同模型。

共同规则和玩法指南分别位于 `configs/prompts/common_rules.md`、`configs/prompts/game_guide.md`。人设可直接填写 `show.profiles.GER.persona`，也可使用 `persona_file`；初始经验可用 `experience` 或 `experience_file`。共同规则、指南和每国提示词都会在开局时冻结。修改后请新开对局，不把新提示词混进已有对局的恢复阶段。

默认五国、两轮公开讨论且全部节目功能开启时，一轮最多约 37 次模型请求：5 次意图、10 次公开发言、5 次私信、至多 5 次回复、5 次决策、1 次裁判、5 次复盘、1 次解说。没有来信的国家不会生成回复。`max_tokens` 是单次输出上限，不是整轮输入或总费用上限；当前通过回合数、讨论轮数、开关和单次输出控制预算，尚无货币预算硬停止。可先用短配置运行一轮，再查看各调用记录中的 `metadata.usage`。

短配置使用 `scoring.mode="exhibition"`，完成回合只展示指标。需要自动排名时，开局前选择 `territory_delta`（终点控制陆地省份数减起点）或 `objectives`（终点控制指定 `province_ids` 的数量），最高分获胜、同分并列。这些是本项目明确规定的比赛分数，不是 HOI4 的原版投降／胜利判定。社区地图须先通过地图索引兼容检查，不能仅凭原版省份 ID 直接使用。

## 6．中断后怎样恢复

仍在同一个游戏进程、游戏保持原状态时，菜单选 4 即可。终端对应命令为：

```powershell
.venv/Scripts/python.exe tools/campaign.py status --name first_show
.venv/Scripts/python.exe tools/campaign.py resume --name first_show --turns 1
```

恢复会使用原配置和原请求：已完成模型调用按相同提示词及参数复用；已生效动作跳过；时钟中断时读取真实日期，核对存档后只推进剩余小时。结果不明且缺少足够核验存档的动作会要求检查，不会盲目重发。

游戏重启后，先载入与记录一致的检查点并暂停，然后使用原比赛配置重新建立控制，再恢复：

```powershell
.venv/Scripts/python.exe tools/campaign.py control --name first_show --config configs/campaign.local.json --refresh
.venv/Scripts/python.exe tools/campaign.py resume --name first_show --turns 1
```

`--config` 必须是该对局原来的国家和规则配置，不要在这里换成另一份五国示例。手动读档或修改了受控状态后，也应重新核对控制状态。恢复时仍须通过日期和玩法状态检查。

若游戏崩溃丢失了只存在内存中的推进时间，旧存档可能落后于日志，不能直接视作最新检查点。保留原记录并核查；无法对齐时，从明确的检查点新开一个对局，记录这次技术中断。不要删掉日志后重跑同一批命令。

## 7．离线检查

离线测试不需要游戏、密钥或外部后端：

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tools -p "test_*.py" -v
python -I tools/parse_save.py --selftest
python -m compileall -q tools
```

GitHub Actions 在 Windows 与 Ubuntu 的 Python 3.12 上运行这些离线检查。工作流使用官方 [checkout](https://github.com/actions/checkout) 和 [setup-python](https://github.com/actions/setup-python)，不下载游戏，不读取模型密钥，也不执行桌面操作。

场景选择见[短局与社区地图](SCENARIOS.md)，项目制作方向见[产品方向](PRODUCT_DIRECTION.md)。
