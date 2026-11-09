# 本地观察席与录制

```powershell
python tools/observer.py --name native_recording_50_20261109 --port 8765
```

打开 <http://127.0.0.1:8765>。面板每3秒刷新进度、日期、阶段、五国公开互动、公开局势变化和近五回合耗时；OBS运行后每10秒提供一次真实游戏捕获预览。完整录像由OBS单独保存，预览图片不是录像本体。

“观众密档”默认关闭，主动开启后可看事前意图摘要、私信、密封命令、裁判审核和执行回执。事前摘要不是模型隐藏思维链。私人材料仅供观察者查看，不作为玩家输入；公开发言与可观察结果仍与密封决定分开。

可标记名场面、疑点和待追踪事项。笔记仅写入本局 `observer_notes.jsonl`，不进入模型上下文。导出的Markdown观察战报来自公开事件和人工笔记，不额外调用总结模型。

## 50回合录制入口

先加载指定存档并暂停，准备原生桥与已验证的sidecar，确认五国原版AI关闭。使用现有OBS portable `IRON_VERIFY` profile和同名scene collection；游戏捕获须为1280×720。密钥读取用户环境变量，不需要桌面令牌文件。

```powershell
python tools/record_session.py --name NEW_MATCH_NAME --config configs/campaign.recording50.json --pid GAME_PID --save-dir "SAVE_DIRECTORY" --obs-config "OBS_WEBSOCKET_CONFIG"
```

录制通过OBS WebSocket开始，在每轮新调用前检查录像仍在运行；50回合完成或运行出错后停止录像。文件存入 `recordings/NEW_MATCH_NAME/`，路径、起止时间和录制状态保存在本局 `recording.json`。新建对局不能覆盖旧局名称。赛后采访与导演点评仍使用 `native_campaign.py --postmatch` 单独补齐，不合成语音或启动剪辑软件。

## 观战画面

- 录制使用OBS原生 `game_capture`，不改为桌面捕获；观察面板或其他窗口不会被叠录进去。
- 开录前收起无用的部队详情、外交/经营面板，移开鼠标避免提示框；不隐藏日期、暂停等必要状态。
- OBS控制窗口及观察面板浏览器在不查看时最小化，游戏置前；不要关闭用户其他窗口或标签页。
- 需要选择的游戏事件必须先保存信息并交给合法决策流程，不能为了画面清爽直接确认。已有API只对已验收的 `news.66` 提供选择和resolved关窗，不能据此宣称通用弹窗均可自动清理。
- 游戏或模型异常时先保留当前存档、回执和录像，不能靠重放已执行命令恢复。

## 新接口环境变量

`IRON_GLM_API_KEY`、`IRON_QWEN_API_KEY`、`IRON_DEEPSEEK_API_KEY`、`IRON_CLAUDE_API_KEY`、`IRON_GPT_API_KEY`、`IRON_GEMINI_API_KEY` 保存于Windows当前用户环境。显式进程环境变量优先。源码、配置、观察接口和Git提交均不包含令牌值。

和平预算改为720小时，战争仍24小时、关键事件6小时。50回合是本轮上限；分段检查可以使某轮提前结束，不能按50×30天推定真实对局跨度。
