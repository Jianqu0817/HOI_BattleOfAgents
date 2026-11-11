# 工具目录

从仓库根目录按任务选择入口。未列出的文件包括被入口导入的模块、专项检查和测试；遇到缺少的游戏动作，先查对应版本的原生动作说明。

| 任务 | 维护入口 | 输入或产物 |
| --- | --- | --- |
| 环境初始化 | [setup.py](setup.py) | Windows 本机环境、共享地图依赖和路径配置 |
| 对局与赛后解说 | [native_campaign.py](native_campaign.py) | 指定场次、模型配置、检查点。赛后模式读取已结束的回合 |
| 对局录制 | [record_session.py](record_session.py) | 协调原生对局和 OBS，生成录像与录制回执 |
| 净地图与镜头 | [native_view.py](native_view.py) | 通过原生接口隐藏 HUD、锁住镜头和定位省份；先加载 view sidecar，见[原生 API](../docs/NATIVE_API.md#净地图与镜头) |
| 本地观察 | [observer.py](observer.py) | 按场次查看公开发言、进度和最近确认日期 |
| 离线核验 | [verify_native_campaign.py](verify_native_campaign.py)、[parse_save.py](parse_save.py) | 原生对局审计、游戏文本存档 |
| 当前合照局开场 | [opening](tyrol/opening/) | 讲课、入场与选国、合并开场 |
| 新桌入场和选国 | [preshow.py](tyrol/opening/preshow.py) | 六位模型依次发言；按场名保存公开原话、选国结果和对局配置 |
| 七日场与配音交付 | [tyrol](tyrol/) | 七日实录的选段、公开总结大会、配音与合片，各脚本使用对应场次的台本和素材 |
| 按场次剪辑新桌 | [session_edit.py](tyrol/session_edit.py) | 校验公开选段，复用现有立绘／参考声线／字幕对齐，合成无 BGM 配音版 |
| 自我介绍音高与语速试听 | [voice_preview.py](tyrol/voice_preview.py) | 对既有五人介绍制作 A/B 对比；支持语速增量或绝对字速、分角色基频目标，独立导出而不改整片 |
| 角色表演指令合成试听 | [voice_instruction_trial.py](tyrol/voice_instruction_trial.py) | 用角色专属 instruction 重新合成六段短句；复用原台词与已注册声线，单独保存请求、小样和旧／新对比 |
| 少女指令与参考声线 A/B | [voice_reference_ab.py](tyrol/voice_reference_ab.py) | 3路并发新合成六人双版本；角色复刻 ID 对照同一系统女声，无参考版不提交角色参考或复刻身份 |
| 参考声线指令迭代试听 | [voice_expression_trial.py](tyrol/voice_expression_trial.py) | 以已交付小样为基线，按 profile 修改 instruction 和明确指定的合成参数，六段并发重合成并逐人对照 |
| MiniMax 配音小样 | [minimax_audio.py](minimax_audio.py) | CLI默认 `speech-2.6-turbo`；本节目当前显式指定2.8 Turbo，使用 `configs/tts.minimax.cast.json` 的新音色；配置样例为 `configs/tts.minimax.cast.example.json`，账户专属配置不分发 |
| MiniMax 六角色全参考克隆与重建 | [minimax_rebuild_cast.py](minimax_rebuild_cast.py) | 全部参考录音、不传 `clone_prompt`、2.8 Turbo；用户授权释放后按固定计划删除旧ID并重建六角色，保留逐步回执和配置备份 |
| v3开场配音接回视频 | [integrate_v3_opening.py](tyrol/integrate_v3_opening.py) | 离线复用已审音轨；`--revision v3_3` 含最新节奏、逐位亮相及抽卡后的电视无信号转场，导出开场和整期接入版 |
| v3.1开场台词与字幕修订 | [revise_v3_opening.py](tyrol/revise_v3_opening.py) | 应用用户逐句修改，仅重合成受影响段；保存TTS原生字级字幕，再用上一入口的 `--revision v3_1` 接回视频 |
| v3.2节奏留白与逐位入场 | [pace_v3_opening.py](tyrol/pace_v3_opening.py) | 补战略／供给前导句，其余配音复用；接片入口用 `--revision v3_2` 加五次留白、选手席转场及累计亮相 |
| v3.4开场配乐混音 | [add_opening_bgm.py](tyrol/add_opening_bgm.py) | `--music` 指定本地配乐；无信号短哔后低音量进入，选手入场转场前淡出，保留v3.3画面、字幕与人声 |
| v3.5全片续制 | [continue_v3_episode.py](tyrol/continue_v3_episode.py) | 保留v3.4开场；后半段按v3稿重配，句末留白、七回合转场及第3／5／7回合局势讲解；可分 `prepare`、`voices`、`render` 阶段续做 |
| v3.6后半段轮播配乐 | [add_episode_bgm.py](tyrol/add_episode_bgm.py) | `--music` 按顺序传入本地曲目，播完列表循环；从选国开始低音量铺到片尾，曲间2秒交叉淡化，保留开场混音、画面及对白节奏 |
| v3.7二十段GLM内心解说 | [insert_glm_inner.py](tyrol/insert_glm_inner.py) | `--script` 读取已审核的20段MD；只配新增解说，居中明亮GLM与轻压暗底图，三段地图讲解替换旧稿并留全图回看，四曲BGM连续轮播 |

`campaign.py` 提供共享配置等函数，`model_trial.py` 实现模型沟通与决策。旧名 `run_turn.py`、`recover_clock.py` 保留了读取或核验历史检查点的函数。桌面版历史资料见[归档](../docs/archive/ui/)。

`runs/` 和 `exports/` 用作数据输入和交付目录。新增可复用脚本放入 `tools/`，更新本表和对应工作流。历史发布快照、临时试验脚本和旧交接记录仅供溯源。
