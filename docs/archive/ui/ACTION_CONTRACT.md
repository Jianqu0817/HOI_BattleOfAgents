# 国家观察与动作接口 v1（旧桌面版归档）

> 本文记录退役时的实现和命令。当前入口见[工具目录](../../../tools/README.md)。

> 旧版 UI 执行示例已退役。当前启动、执行和恢复步骤见[快速上手](../../QUICKSTART.md)。

当前实现位于 `tools/agent_contract.py`，回合执行位于 `tools/run_turn.py`。支持配置中的 3–5 国，默认五国。`tools/campaign.py` 将模型、外交、裁判、执行和素材导出接到同一条执行链；固定 JSON 请求仍可用于独立联调。

## 观察

从同一份确切存档的解析 JSON 为各国分别导出文件：

```powershell
.venv/Scripts/python.exe tools/agent_contract.py observe `
  --save runs/m1_actions/ger_civilian_factory/iron_ger_civilian_factory_after.json `
  --output runs/agent_contract/turn0 --match iron_m1 --turn 0
```

每份观察包含国家、日期、回合、原始解析文件路径及 SHA-256、观察 ID、本国关键玩法状态指纹、政治点数、科研、国策、军工线、建造队列、本国地区和师位置。数据通过明确字段路径读取：政治点数取 `politics.political_power`，队列取 `production.general_lines.building`，生产线装备名称通过稳定装备变体 ID 从顶层 `equipments` 取得。尚未校准的可用人力与空建筑位标记为不可用，不用递归找到的同名字段代替。

候选科技与国策来自本机固定版本游戏和 Mod 文件，附本地名称。脚本条件和当前界面可用性仍在执行前由游戏界面核验。当前名称搜索路径仅支持静态国策名。状态文件标记为 `public_debug`：本国部队与他国隐藏部队已分别过滤，完整战争迷雾和情报可见性仍待验收。

`army` 提供本国师与军团的真实 ID、成员、位置、组织度、原始强度/补给、已确认战争关系及本国参与战斗。只有本机操作验收、军团绑定、同一地图和界面身份均匹配时，才提供对应陆军动作菜单，详见[陆军控制](../../ARMY_CONTROL.md)。

观察 ID 绑定完整观察内容；玩法状态指纹绑定科研、国策、政治、生产、部队和本国地区建筑。执行前重新存档，逐国确认日期和指纹。仅新增回执日志不改变玩法指纹。

## 请求

```json
{
  "match_id": "iron_m1",
  "turn_id": 0,
  "country": "GER",
  "date": "1936.3.15.11",
  "observation_id": "从对应观察中复制",
  "actions": [
    {
      "id": "0-GER-1",
      "type": "research.start",
      "args": {"slot": 2, "technology_id": "construction1"}
    }
  ],
  "reason": "使用空科研槽开始建筑技术研究"
}
```

| 动作 | 参数 | 已实现范围 |
| --- | --- | --- |
| `research.start` | `slot`, `technology_id` | 科研槽从 1 开始；候选科技与可见空槽核验 |
| `focus.select` | `focus_id` | 当前没有国策；详情标题对应唯一 ID，开始按钮可用 |
| `production.assign` | `line_id`, `factories` | 使用存档中的稳定生产线 ID；每次增减一间工厂 |
| `construction.queue` | `state_id`, `building_id`, `count` | 本国地区；`industrial_complex`；每次排入一个 |
| `army.move` | `army_id`, `target_province_id` | 已绑定军团，沿已核实的友方陆路向候选省份移动 |
| `army.attack` | `army_id`, `target_province_id`, `target_state_id` | 普通右键攻击已宣战敌国控制的候选省份，不代替宣战 |
| `army.stop` | `army_id` | 停止已绑定军团的既有计划执行与当前移动 |

每国每轮允许 0–6 个动作，空列表表示维持现有指令。批内动作 ID 与目标不能重复，避免在同一旧状态上连续操作相同目标。未知动作、额外参数、错误国家／回合／观察 ID、布尔值冒充数字，以及外国家地区会在执行前被拒绝。

```powershell
.venv/Scripts/python.exe tools/agent_contract.py validate `
  --observation runs/agent_contract/turn0/GER.json `
  --request runs/agent_contract/requests0/GER.json
```

## 一轮执行

`run_turn.py` 在接触玩法动作前校验所有国家请求，确认游戏暂停、当前进程与已取得 AI 停用回执的进程一致，再核验新存档。按逐轮轮换的顺序切国执行，输入始终串行。

每个动作先将 `unknown` 意图写入日志，然后执行正常界面操作、写新存档并检查目标效果与其他国家状态。界面返回错误时仍以确切存档核查实际结果；只有验证通过才记为 `applied`。结果不明时保持暂停并停止，不自动重试。已有回合日志阻止直接重放。

生产验证分别检查目标请求与实际占用：释放一间工厂后，游戏可以将它补到本国另一条缺额生产线。验证要求其他线的 `requested_factories` 和装备不变，另行记录 `active_factories` 的实际变化。

陆军验证区分命令派发、成员全员或部分响应、推进后移动/到达以及实际交战。`effect_status=partial` 表示已核清部分成员因既有防御战阻塞，不能将 `verified=true` 剪成全军团全部执行成功；完整要求见[陆军控制](../../ARMY_CONTROL.md)。

暂停且尚未开始推进时，可以显式使用 `--resume`。恢复读取原请求与每次确切前后存档：已经生效的动作标为 `applied` 并跳过；五国玩法指纹均不变的失败动作标为 `not_applied` 后才可再次执行。缺少动作后存档、存在无法解释的玩法变化，或已经进入时钟推进阶段时停止恢复。重新存档还必须与恢复检查点逐国一致。

全部动作通过后才按配置推进共同时间，以引擎日志日期核验精确小时数，再为全部国家取得同一暂停时刻的回执和最终存档。每次运行保存完整请求、动作、界面证据、存档路径和时钟回执。

若中断发生在推进阶段，由 `tools/recover_clock.py` 从真实日期与检查点核对剩余小时，不能重新运行整段时间；`campaign.py resume` 会选择相应恢复阶段。最终可运行 `tools/verify_campaign.py --name NAME` 离线核对已冻结证据，缺失或尚未完成的阶段不会报告通过。

默认配置仍推进 24 小时；`configs/match.fifteen_days.json` 支持一次战略回合推进 360 小时，内部逐段按最多 24 小时核验。模型接入和裁判协议见 [模型接口](../../MODEL_INTERFACE.md)。

本次实际命令：

```powershell
.venv/Scripts/python.exe tools/run_turn.py `
  --observations runs/agent_contract/turn0 --requests runs/agent_contract/requests0 `
  --name five_actions_0_verified --known-disabled-pid 34280
```

`--known-disabled-pid` 指调用方已确认五国 AI 停用的当前进程。本次使用已有明确回执的 PID 34280；新进程需要重新建立控制状态。工具不会自动重复 `ai TAG` 切换命令。

当前正常操作配置针对已验收的中文 1280×720 渲染界面；本机实测 Windows 缩放为 125%。陆军使用已绑定军团和已校准操作；当前没有宣战、军团新建/调编、前线绘制、海运/登陆、空海军或通用事件选项接口。预设终点与省份计分规则见[短局与场景](../../SCENARIOS.md)，实际验收范围见[录制版本报告](../../experiments/RELEASE_20261108.md)。
