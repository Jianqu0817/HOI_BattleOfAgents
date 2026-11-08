# 正式配音资产

本目录保存用户选定的六个声线及参考素材；运行配置为 `configs/tts.cast.json`。项目的后期与新对局配置已指向它，生成时只使用工程内副本，不再读取 F 盘原素材。

| 角色槽位 | 声线 | GPT | SoVITS | 参考数（含主参考） |
| --- | --- | --- | --- | --- |
| qwen / 千问 | 妃咲 | v2 e15 | v2 e24 | 4 |
| glm / 智谱主持 | 和纱 | v2 e15 | v2 e24 | 5 |
| gemini / 基米 | 泉奈 | v2 e15 | v2 e24 | 3 |
| gpt / 白色龙娘 | 莉音 | v2ProPlus e15 | v2ProPlus e18 | 4 |
| claude / 克莱德 | 佳代子 | v2 e15 | v2 e16 | 4 |
| deepseek / 大肥鱼 | 宫子 | v2 e15 | v2 e16 | 4 |

每个角色子目录包含 `models/`、`references/` 与 `manifest.json`。总清单 `manifest.json` 记录原始来源、工程相对路径、SHA256、训练轮次、参考时长及逐字转录。模型的新旧按训练轮次选择，不按下载时间，也不声称最高轮次音质一定最好。

每人选一条主参考，其余精选素材均作为辅助参考。用户最新指定 GLM 主参考为 `CH0250_Season_NewYear.wav`，已额外复制到工程；其他五人的主参考从各自精选素材随机抽取一次后固定，具体选择见 `configs/tts.cast.json`。不会每次合成重新抽取。主参考的转录单独送入 API，辅助参考不拼接转录。

用户试听反馈：前一批因停顿过多普遍偏慢。接下来的语速改进应以标点分段，为各段和整句设置大致目标时长，并检查实际时长与段间停顿；保留必要语气，避免靠整体加速或升高音高解决。**此项为待实现的节奏处理要求，目前尚未实现目标时长控制，前一批试听也未按新参考重新生成。**

模型与 WAV 保存在本机并由 Git 忽略；清单、配置、文档可以跟踪。克隆 Git 仓库本身不会获得这些二进制资产，迁移机器需连同整个 `assets/voices/` 一起复制。后端代码与共享基础依赖仍由 `tools/start_gpt_sovits.py` 管理。

从工程根目录启动及配音：

```powershell
.venv/Scripts/python.exe tools/start_gpt_sovits.py
.venv/Scripts/python.exe tools/production.py --episode <素材目录> --config configs/production.example.json --synthesize
```

初次导入使用 `tools/import_voice_assets.py`。它读取本机的 `configs/voices.sources.local.json`，只复制指定精选目录的 WAV 与每人最高轮次的 GPT/SoVITS 文件，核验 SHA256，并生成正式配置。日常合成不需要再次导入，也不需要原始下载目录。

本次统一试听：`runs/cast_voice_audition_20261109/index.html`，每人两条；详细合成回执与音频校验在同目录。

2026-11-09：12 条均经原生 API 实际生成，通过有效 WAV、非静音、台词原文及辅助参考 SHA256 检查，整批合成与导出耗时 117.30 秒。角色配置相关 6 项测试通过。未启动 WebUI、调用云 API 或推进游戏。试听时长约 8.24–17.86 秒；本次保持语速参数 1.0，没有为压到同一时长而重采样。
