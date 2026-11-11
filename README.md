# IRON · HOI Battle of Agents

五位 AI 玩家在同一局《钢铁雄心 IV》中选国、谈判和下令，智谱娘主持。节目同时追踪地图上的局势和桌边的人际关系。参赛国家、角色分配和模型由每场配置决定。

**[快速上手](docs/QUICKSTART.md) · [工具目录](tools/README.md)**

## 从哪里开始

| 我想做什么 | 入口 |
| --- | --- |
| 准备环境、开始或恢复对局 | [快速上手](docs/QUICKSTART.md)和[原生对局说明](docs/NATIVE_CAMPAIGN.md)。Windows 下运行 `Start-IRON.cmd --help` 查看参数。 |
| 配置场景、玩家与主持人 | [场景设计](docs/SCENARIOS.md)和[角色设定](docs/CHARACTERS.md)。 |
| 录制、观察、核对回合 | [录制与观察席](docs/OBSERVER.md)。游戏通过原生 API 操作，OBS 负责采集。 |
| 给已录节目配音、加字幕、剪片 | [制作工具入口](tools/README.md)。配音、字幕、剪辑和混音脚本位于 `tools/tyrol/`。 |
| 查看已有测试的适用范围 | [原生验收](docs/NATIVE_ACCEPTANCE.md)。具体场次的进度以本机该场记录和存档为准。 |

## 一回合如何运行

各国从同一游戏时刻取得观察，讨论公开条件，可交换定向私信，然后提交密封决定。裁判审核后由原生 API 执行，游戏推进到下一个检查点。桌上的承诺、执行回执和存档中的结果分别记录，便于回看时核对。公开主持只能引用公开材料；观众专属采访和复盘按场次配置在赛后生成。

## 角色与舞台

六位角色的[立绘素材](assets/characters/poses/)包含姿势、表情和说话差分。[学院讲堂](assets/stage/academy/README.md)提供讲台前景和大屏背景。剪辑时将半身立绘的下沿藏在桌沿或对话框后面，地图镜头按拍摄当时的游戏日期标注。

| 基米 | 克莱德 |
| --- | --- |
| <img src="assets/characters/poses/gemini/cat_paws/neutral.png" width="200" alt="基米的猫猫拳立绘"> | <img src="assets/characters/poses/claude/book_hold/neutral.png" width="200" alt="克莱德抱书立绘"> |

## 本机数据与开发检查

从项目根目录运行工具。共享配置位于 `configs/`，每场的冻结配置、回执与素材记录写入 `runs/`，交付文件放在 `exports/`。模型密钥从环境变量读取。游戏存档、声线权重、参考 WAV 和原始录像由制作机保存。内部策划、制作工作流、交接记录及账户专属的 `configs/tts.minimax.cast.json` 仅保存在制作机，不随仓库分发。

MiniMax 角色音色配置可从 `configs/tts.minimax.cast.example.json` 复制为 `configs/tts.minimax.cast.json`，再填入自己账户的音色 ID。API 密钥使用 `MINIMAX_API_KEY` 环境变量，不写进配置。

```powershell
python -m unittest discover -s tools -p "test_*.py"
python -I tools/parse_save.py --selftest
```

依赖和素材来源见[第三方声明](NOTICE.md)。
