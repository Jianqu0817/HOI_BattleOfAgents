# IRON · HOI Battle of Agents

让 **3–5 个独立 AI Agent** 在同一局《钢铁雄心4》中经营、谈判和下令，并为视频保留各国的**事前意图、对外表态、实际行动和结果**。

采用 Windows 单进程游戏、结构化观察、普通玩家操作、独立裁判和共同时间。默认 GER、ITA、JAP、ENG、SOV 五国，各国模型、人设、攻略和经验分别配置。

**[快速上手](docs/QUICKSTART.md) · [视频制作工作流](docs/VIDEO_WORKFLOW.md) · [节目设计](docs/PRODUCT_DIRECTION.md)**

原生 Windows API 与实机验收说明：[原生 API](docs/NATIVE_API.md) · [验收记录](docs/NATIVE_ACCEPTANCE.md)。

五国聊天、私信、密封决策、裁判与视频导出已接入[原生模型对局入口](docs/NATIVE_CAMPAIGN.md)，完成了连续五回合、共 75 个游戏日的[实局验收](docs/NATIVE_CAMPAIGN_VALIDATION.md)。底层也提供[确定性军事批量入口](docs/NATIVE_TURN.md)。`campaign.py` 保留为旧 UI 执行器。

## 启动

```powershell
git clone https://github.com/Jianqu0817/HOI_BattleOfAgents.git
cd HOI_BattleOfAgents
python tools/setup.py
```

也可双击根目录 `Start-IRON.cmd`，先选“安装／初始化”，再选“环境检查”和“开始对局”。请准备自己的单人非铁人文本存档并暂停；运行时程序独占游戏输入。

已验证：Windows、HOI4 1.19.4.2、简体中文、1280×720 游戏渲染。其他版本、语言、UI 比例和替换地图需要重新验收。安装器获取固定版本后端，不分发游戏本体。

密钥通过启动器隐蔽输入或进程环境变量提供，配置只记录变量名。个人路径配置、模型密钥、游戏存档、依赖和录制素材不纳入 Git。

## 一回合如何运行

```text
暂停并采集同一时刻观察
 → 各国事前私密意图摘要
 → 两轮公开讨论
 → 定向私信及回信
 → 密封决定与独立裁判审查
 → 串行普通操作与存档核验
 → 共同推进并暂停
 → 本国复盘与经验积累
 → 观众专属解说、回看与素材导出
```

玩家只能看到本国观察和本人参与的私信。全知解说不回流玩家。“私密意图”是模型事前提交的简短摘要，不是隐藏思维链。承诺保留逐字原话、对象、条件与期限；聊天不会自动变成游戏条约、贸易或援助。

## 可用功能

- 3–5 国连续运行、可配置回合长度和动作预算、冻结配置、分阶段恢复、执行顺序轮换。
- 每国、裁判、解说使用各自模型配置；共同规则、游戏说明、人设和本国经验分层维护。
- 已校准的科研、国策、生产请求调整和民用工厂排队。
- 本国军团与师、真实省份控制、已宣战敌对关系；军团绑定与普通移动／停止／攻击先通过本机 `bind/probe` 验收，再向模型开放。
- 本国可见证据生成的赛后经验，附适用条件和置信度，并在后续回合引用；不修改模型权重。
- 固定终点的展演、控制陆地省份增量、指定目标省份三种计分方式，支持并列。
- 离线 HTML 回看、逐条揭示、国家筛选、CSV/JSONL、SRT、旁白草稿、镜头表、截图素材与真实 UTC 场记。

字幕使用建议剪辑时间，须在剪辑软件中与 OBS 等录屏对齐。观众素材含全部秘密，不能作为玩家输入。

## 运行示例

原生模型对局（先按原生 API 文档加载桥接模块，游戏暂停且参赛国原版 AI 已关闭）：

```powershell
.venv/Scripts/python.exe tools/native_campaign.py --name native_show --pid YOUR_PID --save-dir "YOUR_SAVE_DIRECTORY" --prompt-key
.venv/Scripts/python.exe tools/native_campaign.py --name native_show --pid YOUR_PID --save-dir "YOUR_SAVE_DIRECTORY" --resume --prompt-key
```

旧 UI 经营对局：

```powershell
.venv/Scripts/python.exe tools/campaign.py doctor --config configs/campaign.quick.example.json
# 先在启动器中输入密钥，或设置模型文件指定的进程环境变量。
.venv/Scripts/python.exe tools/campaign.py run --name first_show --config configs/campaign.quick.example.json --turns 1
.venv/Scripts/python.exe tools/campaign.py resume --name first_show
```

完成后打开 `runs/campaigns/first_show/index.html`。短配置为五国、3轮、每轮24小时、每国1动作；默认经营配置每轮360小时。复制示例为 `configs/campaign.local.json` 定制。详细操作、预算和恢复边界见[快速上手](docs/QUICKSTART.md)。

## 验证与范围

已完成五国三个 DeepSeek 经营回合（累计45游戏日），以及两轮完整节目试播（累计6游戏日）：意图、公开讨论、私信回复、密封命令、实机进攻与经营、复盘、解说、字幕和回看均已贯通。最新试播1,158项记录审计通过，116项离线测试通过。[首版验收](docs/experiments/RELEASE_20261108.md) · [首次模型联调](docs/experiments/LLM_20261108.md) · [连续回合验证](docs/experiments/CONTINUOUS_20261108.md)

当前工具用于可配置的录制对局。完整战争迷雾、海空军、贸易、游戏条约、和平会议、通用前线计划与原版投降胜负尚未全部接入。自定义计分与原版战争胜负分别记录。社区地图先做兼容验收，不自动启用未经验证的 Mod。

## 开发检查

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tools -p 'test_*.py' -v
python -I tools/parse_save.py --selftest
```

离线测试不需要游戏或 API 密钥。GitHub Actions 在 Windows/Ubuntu 运行离线检查；界面操作单独在 Windows 游戏内验收。

[场景与计分](docs/SCENARIOS.md) · [陆军校准](docs/ARMY_CONTROL.md) · [模型接口](docs/MODEL_INTERFACE.md) · [技术路线](docs/TECHNICAL_ROADMAP.md) · [第三方声明](NOTICE.md)
