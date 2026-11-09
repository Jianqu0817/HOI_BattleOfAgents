# 本地 GPT-SoVITS 配音

**当前制作状态（2026-11-09）：**六个角色的正式声线、模型和参考 WAV 已整理到本机 `assets/voices/`，正式配置为 `configs/tts.cast.json`；二进制资产由 Git 忽略，不会随克隆仓库自动取得。配音暂不生成，先采集并冻结台词，再统一交由服务器批量生成。前期声线试听和单句闭环验证是独立历史验证，不代表本轮制作已生成配音。分句、停顿与目标时长控制尚未实现。

后端已部署在 `D:/GPT-SoVITS_IRON`，独立 Python 3.11.15 虚拟环境。API 为 `http://127.0.0.1:9880` 的官方 `api_v2.py`。当前默认为 CPU、float32、v2ProPlus；没有修改系统 Python 或 Conda 环境。

官方代码固定到 [`04a44eb977586645f33594b72af13a035a2056c6`](https://github.com/RVC-Boss/GPT-SoVITS/tree/04a44eb977586645f33594b72af13a035a2056c6)，代码为 MIT。预训练模型来源为 [lj1995/GPT-SoVITS](https://huggingface.co/lj1995/GPT-SoVITS)，固定 revision `8f8857563c040d542de108a2c18b0425abd6c9c5`；G2PW 与 FFmpeg 来源及 revision 写在 `tools/setup_gpt_sovits.py`。各第三方组件保留原许可证；模型文件不提交进 IRON。

## 启动与复现

在 IRON 根目录运行：

```powershell
python tools/start_gpt_sovits.py
```

启动器隐藏服务窗口，已就绪时复用；9880 被其他服务占用时选择下一空闲端口。实际地址、启动器 PID、CPU 配置与日志路径写在 `D:/GPT-SoVITS_IRON/iron-service.json`。Windows 的 venv 启动器会产生实际服务子进程，服务日志显示其 PID。配置文件中的 `base_url` 需与实际地址一致。

首次在另一目录复现，需已安装 Git、uv，以及编译 pyopenjtalk/jieba_fast 所需的 C++ 构建工具：

```powershell
python tools/setup_gpt_sovits.py --root D:/GPT-SoVITS_IRON
python tools/start_gpt_sovits.py --root D:/GPT-SoVITS_IRON
```

安装器只安装推理所需依赖，不安装 ASR、UVR5 或训练界面依赖。下载约 1.8 GB 推理模型、中文读音模型与 FFmpeg；现有不同 revision 的目录会报错，不能覆盖。完整已安装版本记录在后端目录的 `iron-inference-freeze.txt`。

## 六个声音槽位

**2026-11-09 正式资产：**用户选定六个声线，使用 `configs/tts.cast.json`，模型及所选参考副本位于 `assets/voices/`。对应为 qwen＝妃咲、glm＝和纱、gemini＝泉奈、gpt＝莉音、claude＝佳代子、deepseek＝宫子；详情见 `assets/voices/README.md`。`tts.gpt_sovits.example.json` 是空参考的通用模板，不是当前正式配置。

正式配置包含 `deepseek / claude / qwen / glm / gemini / gpt` 六个角色 ID，独立于国家与模型分配。各槽位使用 `assets/voices/` 中对应的基础/角色权重和参考副本，准确清单与哈希以 `assets/voices/manifest.json`、`configs/tts.cast.json` 为准。注意仓库忽略这些大文件；只有本机已有资产时才能直接使用。

每个角色填写：

- `ref_audio_path`：后端可读取的、获授权使用的干净单人语音，建议 3–10 秒；避免音乐与重叠说话。
- `aux_ref_audio_paths`：可选的辅助参考录音路径数组，例如 `["F:/voices/secondary.wav"]`。主参考仍使用自己的 `prompt_text`；辅助录音不拼接转录。客户端会检查辅助文件是否存在，并将其路径和文件 SHA256 纳入缓存及回执；修改辅助文件会使旧缓存失效。不填写或使用空数组时沿用单参考流程。
- `prompt_text`：参考语音的准确逐字台词。
- `prompt_lang`、`text_lang`：参考语音和合成文本语言，中文使用 `zh`。
- `gpt_weights`、`sovits_weights`：后续若有角色专属训练权重，可替换现有基础权重。

### 一条主参考与多条辅助参考

在同一个角色对象中填写数组即可；项目配音入口会把全部辅助参考传给原生 API：

```json
{
  "ref_audio_path": "F:/voices/main.wav",
  "prompt_text": "主参考录音的准确台词",
  "prompt_lang": "zh",
  "text_lang": "zh",
  "aux_ref_audio_paths": [
    "F:/voices/aux_1.wav",
    "F:/voices/aux_2.wav",
    "F:/voices/aux_3.wav"
  ]
}
```

辅助录音建议来自同一角色且干净、音质接近，逐条选择，不把多条录音拼接成一个文件。不需要为辅助录音填写转录；`prompt_text` 只对应主参考。客户端没有限制辅助条数，但更多录音不保证更好，也会增加处理开销。当前 v2 后端对参考特征求平均，不提供每条参考的权重比例。

所有辅助文件在请求前逐条检查，任意文件缺失则返回 `missing_reference`。添加、删除或修改任意辅助录音都会重新计算缓存身份；返回以前使用过的组合时可以复用它已有的缓存。空数组和省略字段兼容原单参考缓存。每条辅助文件的路径和 SHA256 都保存在 `voice_receipts.json` 的 `sources.aux_ref_audio_paths`，方便复现。

将配置保存为 `configs/tts.<角色>.local.json`，并在后期配置的 `tts_config` 指向它。运行方式保持不变：

```powershell
.venv/Scripts/python.exe tools/start_gpt_sovits.py --root D:/GPT-SoVITS_IRON
.venv/Scripts/python.exe tools/production.py --episode <素材目录> --config <后期配置.json> --synthesize
```

角色卡与声线描述不能替代参考录音。正式资产已有主参考和辅助参考路径，GLM 主参考固定为 `CH0250_Season_NewYear.wav`，其余角色从精选素材中各抽取一次后固定；具体路径以配置为准。本节描述 GPT-SoVITS 的声线资产。千问配音的逐句参考选样见[视频制作工作流](VIDEO_WORKFLOW.md)。下文的试听和单句出声记录属于此前独立验证。

### 多辅助参考验证（2026-11-09）

复用原生 `aux_ref_audio_paths` 数组，已通过 **12 项定向测试**，覆盖三条辅助录音完整传入、中间录音内容修改、参考增删、空数组兼容、后续参考缺失和后期导出链路。

真实验证使用佳代子 `CH0239_EventLogin_2.wav` 为主参考，`CH0086_EventLobby_2_1.wav` 与此前试听用过的 `CH0086_Cafe_monolog_3.wav` 为两条辅助参考。通过 `tools/production.py --synthesize` 生成一条 **11.24 秒**的非静音 WAV，回执含两条辅助录音 SHA256，原始素材未变；含导出和检查约 **9.06 秒**。未启动 WebUI、未调用云服务、未推进游戏，也未修改正式声线分配。

本机可用配置为 `configs/tts.kayoko.multiref.local.json`；此次只在独立试听目录将佳代子声线临时用于 `claude`。输入快照、配置、回执、播放器与 `verification.json` 在 `runs/gpt_sovits_multiref_20261109/`。音色优劣仍以试听为准。

## 冻结台词接入

`tools/gpt_sovits.py` 接受 `VoiceLine` 或同名字段字典：`line_id, character_id, text, event_id, visibility`。`synthesize(config, line, output_dir)` 保留原文与可见性元数据，只为已冻结台词生成 WAV；不调用游戏操作或改写台词。观众/公开导出应由调用方按自己的可见性规则筛选输入。

返回状态包括 `generated`、`cached`、`missing_voice`、`missing_weights`、`missing_reference`、`invalid_line`、`failed`。`backend_status(config)` 独立返回 API 可用状态，不能用来证明某个声音已可合成。

缓存键包含原文、角色 ID、模型/参考文件 SHA256、参考转录、语言、参数与后端 revision；WAV 验证后原子落盘。后端全局模型切换与合成串行，避免六个角色并发切换权重。共享该 API 的其他程序也应避免同时切换模型。请求超时返回失败，不自动重复昂贵合成。如果进程在持有锁时被强制结束，确认已无该合成进程后再删除临时目录中的 `iron-gpt-sovits-*.lock`。

## 历史部署与接口验证

本节保留已发生验证的时间与范围；不表示当前制作批次已开始合成。

2026-11-08：官方 v2 GPT/SoVITS、BERT、CNHuBERT 加载完成，`GET /openapi.json` 返回 200；真实 `POST /tts` 缺少参考声音时返回 400 `ref_audio_path is required`。六个角色槽位报告 `missing_reference`。客户端三项测试通过：缺参考不请求、缓存与原始台词来源保留/参考变更失效、错误 JSON 不落成 WAV。尚未验证实际合成音色、速度或六个角色的声音区分。

2026-11-09：用户要求切换 v2ProPlus，并提供 `F:/BAIDUPAN/莉音` 试听。复用本机 v5 整合包中的 `s1v3.ckpt`、`s2Gv2ProPlus.pth` 和 ERes2Net 声纹权重，复制至隔离部署并核对 SHA256，没有重新下载。

- 原生 API 仍为 `http://127.0.0.1:9880`，当前加载莉音 GPT `LiYin-e15.ckpt` 与 SoVITS `LiYin_e18_s324.pth`，CPU/float32。它们是此次选择的最新保存轮次，不代表已经比较确认最优轮次。
- 独立试听 WebUI：`http://127.0.0.1:9875`，复用用户整合包的运行环境，模型列表已确认选中上述莉音权重。原 v5 Turbo 试听界面 `http://127.0.0.1:9872` 保留。整合包内两个 `*_weights_v2ProPlus` 目录通过 Windows junction 指向用户模型目录，不复制全部角色权重。
- `configs/tts.liyin.local.json` 是独立试听配置，使用包内 `all.zip` 的约 3.90 秒日语参考录音 `ch0158_cafe_monolog_3.wav` 和 `all.txt` 对应日文转录，参考语种 ja、输出语种 zh。原始用户模型与压缩包没有改写。
- 六个正式角色的默认权重已升级为 v2ProPlus 基础权重，各自语速保留；莉音尚未绑定到其中某位角色，不能把同一试听声线声称为六种独立声线。
- API 就绪、WebUI 选中模型、模型与参考文件存在检查通过；客户端 3 项定向测试通过。用户本轮试听反馈“效果还不错”，因此未另发起重复合成。此次未另行验证 IRON 自动台词导出到 API 的完整出声链，也没有进行多轮次或 v5/v2ProPlus 音质对比。
- 配置切换前的备份、选择记录、资源 SHA256 与 WebUI 日志在 `runs/tts_v2proplus_liyin_20261109/`。v2 原权重保留，源码提交版本未变；安装脚本默认资源与配置也已改为 v2ProPlus，避免后续安装重置回 v2。

## 项目自动配音闭环（2026-11-09）

已通过项目自带入口完成一次真实出声验证，没有启动或调用 WebUI，没有推进游戏或调用参赛模型 API。

```powershell
.venv/Scripts/python.exe tools/start_gpt_sovits.py --root D:/GPT-SoVITS_IRON
.venv/Scripts/python.exe tools/production.py --episode runs/auto_tts_liyin_20261109 --config runs/auto_tts_liyin_20261109/production.json --synthesize
```

这次输入为已完成第 29 回合的公开台词 `T29-E013`，原文逐字保留，使用莉音 GPT e15 / SoVITS e18 合成。参考音频实际来自 `F:/BAIDUPAN/莉音/all/all/ch0158_cafe_monolog_3.wav`，约 3.90 秒；转录取自包内 `all.txt`，日语参考、中文输出。

- 生成 1 条有效 WAV：**7.60 秒、32,000 Hz、单声道、16-bit PCM**，检测到有效非静音信号。音质与角色表现仍由试听判断。
- 同时生成 `voice_lines.jsonl`、`voice_receipts.json`、`characters.json`、`editing_plan.json` 和带音频播放器的 `cast.html`。
- 再执行同一命令返回 `cached`，音频 SHA256 与修改时间不变，API 日志新增合成请求为 **0**。
- 校验台词与事件 ID、公开可见性未变，原始回合文件 SHA256 未变。此次只在独立验证目录为意大利角色临时使用莉音配音，不改变正式角色声线分配。
- 从冻结验证样本至检查完成约 **187.53 秒**，包含依赖修复和缓存验证，不是单条音频的纯推理时间。
- 发现并修复隔离部署缺少 `resampy==0.4.3` 与 `fast_langdetect/lid.176.bin`；语言识别模型复用本机整合包文件并校验 SHA256，安装脚本同步补齐二者。后端依赖快照已更新。
- 配音 CLI 现在会在 `--synthesize` 未生成全部所选台词时返回非零退出码，同时保留失败回执。相关 **8 项定向测试通过**。

证据目录：`runs/auto_tts_liyin_20261109/`。其中 `voice_receipts.generated.json` 保留首次生成回执，`voice_receipts.json` 为后续缓存回执，`verification.json` 记录完整校验结果，`source_manifest.json` 记录原始台词和参考音频来源。

此结果验证了单条真实公开台词的“冻结素材 → 配音 API → 音频文件 → 回执 → 后期播放器 → 缓存复用”流程。没有批量合成整个回合，也没有自动启用新对局中的六角色配音。
