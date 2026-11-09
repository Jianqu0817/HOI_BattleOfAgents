# HOI4 基础知识来源与阅读范围

## 来源身份

- 原始百科： [Hearts of Iron 4 Wiki on Paradox Wikis](https://hoi4.paradoxwikis.com/Hearts_of_Iron_4_Wiki)。这是 HOI4 的 Paradox 社区 Wiki，并非 Paradox Interactive 官方规则/接口文档。
- 可访问镜像： [钢铁雄心 IV 中文离线资料库](https://cooderatom.github.io/hoi4-zh-wiki/)。该 GitHub Pages 站自述其为上述社区 Wiki 的简体中文离线镜像，内容 CC BY-SA 3.0、与 Paradox 无隶属；其首页标示抓取时间 2026-09-13、原站版本 hub、收录 655 页。镜像不是由 Paradox 发布的官方仓库。本次检索未发现可核实来源且覆盖所需玩法页的官方 GitHub Wiki 导出仓库。
- 访问限制：联网工具访问 `hoi4.paradoxwikis.com` 页面时遇到 401/访问失败。本交付据镜像标明的原页面名阅读，并以 IRON 当前项目文档校准 API 边界。镜像抓取日期不是 HOI4 游戏版本号，不保证内容与本机游戏补丁完全一致。
- 相关 GitHub 仓库核查：`thelight0211/hoi4-wiki` 仅能确认包含多个模组制作页面；`cpntodd/HOI4-MCP` 中的 `paradox_wiki/` 也是面向模组制作的页面摘录。两者不作为本指南所需玩法机制的主要来源，避免把不完整镜像当成整部百科。

## 页面阅读范围

| 主题 | 镜像页面 | 阅读重点 |
|---|---|---|
| 时间/暂停 | [Beginner's guide](https://cooderatom.github.io/hoi4-zh-wiki/Beginner%27s_guide.html), [Mechanics](https://cooderatom.github.io/hoi4-zh-wiki/Mechanics.html) | 实时推进、暂停、游戏日期与操作时机的一般概念。 |
| 政治/外交/战争目标 | [Government](https://cooderatom.github.io/hoi4-zh-wiki/Government.html), [Diplomacy](https://cooderatom.github.io/hoi4-zh-wiki/Diplomacy.html), [Faction](https://cooderatom.github.io/hoi4-zh-wiki/Faction.html) | 政治资源、战争目标、宣战合法性、阵营关系和参战影响。 |
| 陆军移动/战斗/组织度 | [Land warfare](https://cooderatom.github.io/hoi4-zh-wiki/Land_warfare.html), [Land battle](https://cooderatom.github.io/hoi4-zh-wiki/Land_battle.html) | 部队、战斗结算、宽度、组织度和不同战斗修正的概念。 |
| 补给/地形 | [Logistics](https://cooderatom.github.io/hoi4-zh-wiki/Logistics.html), [Terrain](https://cooderatom.github.io/hoi4-zh-wiki/Terrain.html) | 补给来源/运输、地形与天气对移动和战斗的影响。 |
| 空军任务 | [Air missions](https://cooderatom.github.io/hoi4-zh-wiki/Air_missions.html), [Air warfare](https://cooderatom.github.io/hoi4-zh-wiki/Air_warfare.html) | 任务类型、任务效率、航程、制空、战斗支援和侦察机制。 |
| 海军任务 | [Naval missions](https://cooderatom.github.io/hoi4-zh-wiki/Naval_missions.html), [Navy](https://cooderatom.github.io/hoi4-zh-wiki/Navy.html), [Naval battle](https://cooderatom.github.io/hoi4-zh-wiki/Naval_battle.html) | 特遣队任务、海域、发现/交战、运输与海战的一般规则。 |
| 投降/胜负 | [State](https://cooderatom.github.io/hoi4-zh-wiki/State.html), [Faction](https://cooderatom.github.io/hoi4-zh-wiki/Faction.html), [Peace conference](https://cooderatom.github.io/hoi4-zh-wiki/Peace_conference.html) | 核心州与投降判断、阵营主要国家、战争结束与和平会议。未扩展到各国特殊脚本。 |
| 科研/生产/建设/国策 | [Research](https://cooderatom.github.io/hoi4-zh-wiki/Research.html), [Production](https://cooderatom.github.io/hoi4-zh-wiki/Production.html), [Construction](https://cooderatom.github.io/hoi4-zh-wiki/Construction.html), [National focus](https://cooderatom.github.io/hoi4-zh-wiki/National_focus.html) | 仅概述相互依赖和基础术语，不给本局国家路线或操作命令。 |

## 项目文档交叉核对

- `configs/prompts/native_guide.md` 与 `docs/NATIVE_CAMPAIGN.md`：五国在同一暂停时刻观察；实际推进预算以当前 `configs/campaign.native.example.json` 的自适应 `clock_policy` 和本轮执行记录为准（示例：和平 720h（约30天）、战争 24h、关键 6h、最大 720h），事件可能提前暂停。`docs/NATIVE_CAMPAIGN.md` 中固定 360 小时的旧描述不代表当前时钟配置。当前可选普通移动、合法宣战及已暴露的海空任务/区域；模型必须从真实观察中选择动作候选。路径/任务回执不代表抵达或战斗胜利；战斗详情仅按本轮实际观察/回执证据表述。
- `configs/prompts/common_rules.md` 与 `configs/campaign.native.example.json`：公开/私信/密封决定有不同路由和可见范围；可以自愿披露本国计划或转述本人参与的私信，也可以说辞、隐瞒或欺骗。私信回复/转述仍经私信路由，不自动公开。系统不会自动公开密封命令，且 Agent 不应访问或泄露未授权他国秘密。
- `docs/ACTION_CONTRACT.md`：更早的界面执行路线曾实现其他玩法动作；不应把那份旧路线清单误认为当前原生对局 API。当前 `native_campaign.py` 的具体范围以 `docs/NATIVE_CAMPAIGN.md`、原生指南和本场 `action_options` 为准。

## 不在本次范围

未逐页通读 655 页 Wiki，没有逐国核实 GER/FRA/ITA/AUS/SWI 国策与初始库存，也未计算具体师编制、飞机/舰船数值、胜率或剧情路线；未将 Wiki 表格数值写成适用于本地游戏构建的硬参数。也未阅读/复述本局密封决策或私信。外部知识帮助理解机制，不能替代当前回合观察、进程/版本验收、游戏读回与冻结比赛规则。
