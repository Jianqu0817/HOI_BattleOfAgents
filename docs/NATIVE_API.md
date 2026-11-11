# 原生 API（当前阶段）

IRON 的 Windows 原生桥通过本地命名管道收发请求，由 HOI4 主线程在原版 Tick 返回后派发。Python 客户端是 `tools/native_rpc.py`；桥接协议和可用操作见 `native/iron_bridge/README.md`。

## 构建与版本

- Windows x64；Visual Studio 2022 的 MSVC x64 C++ 工具和 Windows SDK。构建脚本通过 `vswhere` 定位 Visual Studio，并调用 `vcvars64.bat` 与 `cl.exe`，使用 C++20。
- 本机当前 MSVC 工具集为 `14.44.35207`；构建脚本不把该次要版本硬编码为唯一允许版本。
- MinHook 源码位于 `.deps/minhook`，固定 revision `c3fcafdc10146beb5919319d0683e44e3c30d537`。
- 首次运行 `pwsh -File native/iron_bridge/setup-dependency.ps1` 获取该版本；已有其他版本会拒绝覆盖。
- 仅接受 HOI4 `6ae9f874_35ea878`，EXE SHA-256 为 `bccc62909b6a1ec2c1d5a46ef18608e4d0c7cdc89ad341b7402a23c40f7a8037`；不匹配时拒绝安装 Tick hook。
- `pwsh -File native/iron_bridge/build.ps1` 只编译，输出默认在 `runs/native-build`；不会连接或注入游戏。`python tools/test_native_rpc.py` 使用自有合成测试宿主。

## 请求和回执

管道名为 `\\.\pipe\iron-native-<PID>`，每连接发送一行 UTF-8：`request_id<TAB>operation<TAB>payload<LF>`。ID 限 1–64 个 ASCII 字母、数字、`-`、`_`、`.`。同一桥接进程内，相同 ID 与内容重取原回执；相同 ID 改内容会拒绝。Python 客户端不自动重试。传输超时表示结果未知；保留原 ID 查询回执，不要换新 ID 重发动作。

顶层 JSON 的 `ok` 表示 RPC 是否成功派发；`native_move` 的 `result` 是嵌套 JSON，必须再检查其中的 `ok`。`build_unit TAG UNITID FROM TO` 只构造、核验并销毁命令，不提交。`post_unit ...` 提交普通移动命令，队列接受回执只说明命令被接受。单位后续路径和位置变化需另用 `inspect_unit TAG UNITID` 读取；当前位置等于目标省份才证明到达。若仍有路径，应继续记录后续行军，不能标为停留。`units TAG` 列出当前控制国家的单位快照。

2026-11-08 已在当前 Windows 游戏中验证普通移动、控制国与 AI 切换、存档、宣战目标提交，以及空军任务与海军区域命令。回执只说明原版命令队列接受；效果需另行读取游戏状态。详情见 [NATIVE_ACCEPTANCE.md](NATIVE_ACCEPTANCE.md)。此 API 不提供 UI 或控制台回退路径。

`tools/native_game.py` 提供带本地执行记录的类型化入口，检查两层 `ok`，先写意图再提交，已收回执的请求不会重发。例如：

```powershell
python tools/native_game.py --pid <PID> --journal runs/native_trial --request-id observe_1 units AUS
python tools/native_game.py --pid <PID> --journal runs/native_trial --request-id build_1 build AUS 1001 688 3684
python tools/native_game.py --pid <PID> --journal runs/native_trial --request-id move_1 move AUS 1001 688 3684
```

示例中的单位和位置只对应验收时的局面，使用前必须重新读取。桥接回执缓存仅在本次游戏进程有效；重启游戏后不得用未解决的旧请求继续提交。

原生时钟扩展通过 `native/iron_bridge/build-clock.ps1` 单独构建，加载 `iron_clock_v1.dll` 后注册 `native_clock_v1`。`status` 读取暂停和剩余小时；`build_pause` 仅检查暂停命令构造；`step HOURS` 先设置引擎定时暂停，再提交普通取消暂停命令。日期原始值由 `native_control_v1` 的 `status TAG` 提供。

控制与存档 sidecar 通过 `build-control.ps1` 构建，注册 `native_control_v1`；外交通过 `build-diplomacy.ps1` 构建，注册 `native_diplomacy_v2`；海空军通过 `build-air-navy.ps1` 构建，注册 `native_air_navy_v2`。加载 sidecar 后先查询 `main_status` 和对应操作的只读状态，再用唯一 request ID 执行。必须显式选择正在运行的游戏 PID 并使用 injector 加载 DLL；加载成功不等于注册就绪，需查询操作确认。可用 payload 与验收范围见 [桥接说明](../native/iron_bridge/README.md)。

控制验收中，AUS、GER、FRA、ITA、SWI 的控制权依次切换，五国 AI 均关闭且逐国读回；全局 AI 标志仍为开启。另验证 AUS 存档请求并独立确认生成的 51 MB 文件。日期原始值 `60762794` 解码为 `1936-05-24T02:00:00`；存档文本为 `1936.5.24.3`，其小时字段采用不同编号，回合推进按原始小时差核对。

五国批量执行入口及视频事实记录见 [NATIVE_TURN.md](NATIVE_TURN.md)。空军效果核验检查请求的任务位是否启用或停用，并保留完整实际掩码；游戏可以自动取消互斥任务。

五国模型聊天、密封决策与裁判现已接入 `tools/native_campaign.py`，并完成五个 360 小时回合，见 [使用说明](NATIVE_CAMPAIGN.md) 与 [实局验收](NATIVE_CAMPAIGN_VALIDATION.md)。旧 UI 执行器已移除。本次未把任务设置验收等同于真实海空战。

## 净地图与镜头

`pwsh -File native/iron_bridge/build-view.ps1` 构建独立的 `iron_view_v3.dll`。
向当前游戏 PID 加载后，用 `tools/native_view.py` 读取状态、隐藏 HUD、锁住镜头和定位省份。
它通过主线程调用游戏自身的显示／相机函数，不发送按键、不移动鼠标、不抢焦点。

```powershell
python tools/native_view.py --pid YOUR_PID --journal runs/YOUR_MATCH/view --request-id view-status status
python tools/native_view.py --pid YOUR_PID --journal runs/YOUR_MATCH/view --request-id view-lock lock_camera 0 1
python tools/native_view.py --pid YOUR_PID --journal runs/YOUR_MATCH/view --request-id view-frame frame_province 671 90
python tools/native_view.py --pid YOUR_PID --journal runs/YOUR_MATCH/view --request-id view-hide hud 0 1
```

以上状态参数须以本次读回为准；`hud` 的 `1` 表示隐藏。修改要求游戏暂停。
定位请求返回后，等镜头停稳，从 OBS 预览确认拍到目标区域，再启动录制。
蒂罗尔军事镜头用高度 90：山口、前线和部队活动占据主要画面。高度 310 的中欧全景只用于交代五国位置，不代替战场近景。
恢复手动观看时，先查询状态，再用 `hud 1 0`、`lock_camera 1 0`。
版本限定、原生地址证据和测试入口见[桥接说明](../native/iron_bridge/README.md#native-recording-view)。

## 模型输出与原始记录

私信回复若仅漏写顶层 `country`，严格聊天路由可从该次隔离请求绑定的国家补齐身份字段，记录 `bound_reply_author.v1` 回执；正文、收件人和原始响应保留。显式写错国家仍拒绝，密封动作不使用这项处理。

原生密封请求的身份字符串若被模型插入换行或空白，只有移除空白后与本次观察原值完全一致时才接受，并记录 `observation_identity_whitespace.v1`。旧日期、不同观察 ID 仍拒绝；动作、参数、理由和原始响应不改写，裁判审核的是规范化后实际执行的完整请求。离线审计会重新推导并核对这两类格式回执。

若仅场次号写错、其余四项身份均精确匹配本次观察，允许原模型做一次有缓存的场次号确认。确认只能替换 `match_id`，任何动作、理由或其他字段变化都拒绝；原响应、确认请求和裁判审核后的请求分别留档。
