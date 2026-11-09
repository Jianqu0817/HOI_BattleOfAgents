# 工具目录

从仓库根目录按任务选择入口。未列出的文件包括被入口导入的模块、专项检查和测试；遇到缺少的游戏动作，先查对应版本的原生动作说明。

| 任务 | 维护入口 | 输入或产物 |
| --- | --- | --- |
| 环境初始化 | [setup.py](setup.py) | Windows 本机环境、共享地图依赖和路径配置 |
| 对局与赛后解说 | [native_campaign.py](native_campaign.py) | 指定场次、模型配置、检查点。赛后模式读取已结束的回合 |
| 对局录制 | [record_session.py](record_session.py) | 协调原生对局和 OBS，生成录像与录制回执 |
| 本地观察 | [observer.py](observer.py) | 按场次查看公开发言、进度和最近确认日期 |
| 离线核验 | [verify_native_campaign.py](verify_native_campaign.py)、[parse_save.py](parse_save.py) | 原生对局审计、游戏文本存档 |
| 当前合照局开场 | [opening](tyrol/opening/) | 讲课、入场与选国、合并开场。制作步骤见[视频工作流](../docs/VIDEO_WORKFLOW.md) |
| 七日场与配音交付 | [tyrol](tyrol/) | 七日实录的选段、公开总结大会、配音与合片，各脚本使用对应场次的台本和素材 |

`campaign.py` 提供共享配置等函数，`model_trial.py` 实现模型沟通与决策。旧名 `run_turn.py`、`recover_clock.py` 保留了读取或核验历史检查点的函数。桌面版历史资料见[归档](../docs/archive/ui/)。

`runs/` 和 `exports/` 用作数据输入和交付目录。新增可复用脚本放入 `tools/`，更新本表和对应工作流。历史发布快照、临时试验脚本和旧交接记录仅供溯源。
