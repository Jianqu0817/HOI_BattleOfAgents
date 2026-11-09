# 海空军任务与本机校准（旧桌面版归档）

> 本文记录退役时的实现和命令。当前入口见[工具目录](../../../tools/README.md)。

`tools/naval_air_actions.py` 读取本国舰队和空军联队，通过普通玩家界面下令，再用同一暂停时刻的前后存档核验。没有真实舰队或联队的国家会明确标记不可用，不为内陆国家虚构海军。

| 动作 | 参数 | 核验结果 |
| --- | --- | --- |
| `air.assign` | `wing_id,region_id,mission` | 指定联队任务位与实际战略空域一致；任务为 `air_superiority` 或 `cas` |
| `air.stop` | `wing_id` | 指定联队任务位清零；不声称飞机瞬间返回基地 |
| `navy.assign` | `fleet_id,region_id,mission` | 指定舰队与全部特遣舰队的区域和任务一致；任务为 `patrol` 或 `strike_force` |
| `navy.move` | `fleet_id,province_id` | 所有特遣舰队得到普通玩家路径，终点为指定海洋省份；没有瞬移 |
| `navy.stop` | `fleet_id` | 全部特遣舰队驻留并停止移动 |

执行能力按本机真实探测登记。仅有源码接口或可读取对象，不代表该对象、目标和任务已在当前界面验收。最新实测范围见发布实验记录。

## 读取与身份

舰队位于 `countries/TAG/units/fleet`，空军位于顶层 `strategic_air/TAG/air_wing_pool`。空军目标字段为 `mission.strategic_region`；`mission.type` 是任务位集合，制空为 1、密接支援为 2。驻地从 `strategic_air.air_base` 映射到州。

候选只包含本国对象和已验收的目标。界面选择使用完整精确姓名；短数字名优先用英文 OCR 读取，不将相似中文行或相近数字当作同一对象。空军还要求界面确认仅选中一个联队，不能在选择失败时扩大成整个基地。

后端原有海空工具按英文界面编写，部分路径允许模糊姓名或全基地选择。本模块不直接使用这些宽松选择路径。

## 两阶段绑定

绑定先保存基线，随后由操作者在游戏中选中本国对象。这样不会在改名前保存时因为控制台开合丢掉详情窗口。以下 `PID`、`MATCH_CONFIG`、对局名与数字标记需替换成实际值。

```powershell
.venv/Scripts/python.exe tools/naval_air_actions.py bind-begin --name fra_air_binding --country FRA --domain air_wings --numeric-name 71000001 --base-id 105 --known-disabled-pid PID --match-config MATCH_CONFIG
```

该步骤保存基线并返回地图。操作者打开本国基地中一个联队的详情窗口，确认是可编辑的名称框，然后运行：

```powershell
.venv/Scripts/python.exe tools/naval_air_actions.py bind-selected --name fra_air_binding
```

`bind-selected` 不先切国或打开控制台。它给当前可见名称框写入唯一数字标记，保存后发现实际变名的联队 ID；只有本国、同基地、同日期且除此名称外状态不变，才登记绑定。它不会预设选中了某个期望 ID。必要时按实际截图提供 `--name-region x,y,width,height`。

舰队类似，`bind-begin` 使用 `--domain fleets`，然后在海军界面打开本国舰队名称。数字名应为 6–10 位，避免与普通飞机或舰艇数量混淆。操作日志与存档保存在 `runs/naval_air/<name>/`。

## 探测后才开放动作

绑定成功写入 `configs/naval_air.local.json`，但 `accepted_actions` 仍为空。使用探测入口执行一次实际动作，例如：

```powershell
.venv/Scripts/python.exe tools/naval_air_actions.py probe --name fra_air_superiority --country FRA --action air.assign --wing-id ACTUAL_WING_ID --region-id ACTUAL_REGION_ID --mission air_superiority --known-disabled-pid PID --match-config MATCH_CONFIG
```

`ACTUAL_WING_ID` 使用绑定结果中的实际 ID，区域来自当前安装地图。探测对本次请求临时开放该目标，普通模型菜单仍关闭；只有执行后存档核验通过，才登记动作、任务和目标。CAS、取消任务、舰队巡逻、移动和驻留分别探测。每次探测使用新名称；结果不明时先检查现有前后存档，不重发同一命令。

查看当前登记：

```powershell
.venv/Scripts/python.exe tools/naval_air_actions.py status --country FRA
```

本机配置可以通过 `configs/live.local.json` 的 `iron_naval_air_profile` 指向其他校准文件。更换 UI 尺寸、地图或对象名称后，原证据可能不再适用；执行器会核对当前尺寸、精确名称和候选目标，无法确认则停止。

## 视频与复盘

观众记录分别展示“请求执行”“前后存档确认”和“实际结果”。宣战只在已确认新的战争状态时标为生效；对已交战对象不会剪成第二次宣战。适配器失败不归因于玩家背叛。各国复盘只接收本国任务和已确认的公开战争状态，不读取他国的秘密宣战理由或空军配置。
