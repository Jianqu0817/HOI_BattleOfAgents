# 本地 GPT-SoVITS 配音

后端已部署在 `D:/GPT-SoVITS_IRON`，独立 Python 3.11.15 虚拟环境。API 为 `http://127.0.0.1:9880` 的官方 `api_v2.py`。使用 CPU、float32、v2 官方基础权重；没有修改系统 Python、Conda 环境或正在运行的其他模型。

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

`configs/tts.gpt_sovits.example.json` 包含 `deepseek / claude / qwen / glm / gemini / gpt` 六个角色 ID，独立于国家与模型分配。当前槽位共用官方基础 GPT/SoVITS 权重，**尚未配置六个角色的参考声音**。

每个角色填写：

- `ref_audio_path`：后端可读取的、获授权使用的干净单人语音，建议 3–10 秒；避免音乐与重叠说话。
- `prompt_text`：参考语音的准确逐字台词。
- `prompt_lang`、`text_lang`：参考语音和合成文本语言，中文使用 `zh`。
- `gpt_weights`、`sovits_weights`：后续若有角色专属训练权重，可替换现有基础权重。

角色卡与声线描述本身不能替代参考录音。未提供参考素材时返回 `missing_reference`，不会生成假音频，也不会声称完成六种独立声线。没有从用户私人音频目录寻找样本。官方仓库没有可直接使用的附带 WAV，因此本次未执行真实出声验证。

## 冻结台词接入

`tools/gpt_sovits.py` 接受 `VoiceLine` 或同名字段字典：`line_id, character_id, text, event_id, visibility`。`synthesize(config, line, output_dir)` 保留原文与可见性元数据，只为已冻结台词生成 WAV；不调用游戏操作或改写台词。观众/公开导出应由调用方按自己的可见性规则筛选输入。

返回状态包括 `generated`、`cached`、`missing_voice`、`missing_weights`、`missing_reference`、`invalid_line`、`failed`。`backend_status(config)` 独立返回 API 可用状态，不能用来证明某个声音已可合成。

缓存键包含原文、角色 ID、模型/参考文件 SHA256、参考转录、语言、参数与后端 revision；WAV 验证后原子落盘。后端全局模型切换与合成串行，避免六个角色并发切换权重。共享该 API 的其他程序也应避免同时切换模型。请求超时返回失败，不自动重复昂贵合成。如果进程在持有锁时被强制结束，确认已无该合成进程后再删除临时目录中的 `iron-gpt-sovits-*.lock`。

## 已验证

2026-11-08：官方 v2 GPT/SoVITS、BERT、CNHuBERT 加载完成，`GET /openapi.json` 返回 200；真实 `POST /tts` 缺少参考声音时返回 400 `ref_audio_path is required`。六个角色槽位报告 `missing_reference`。客户端三项测试通过：缺参考不请求、缓存与原始台词来源保留/参考变更失效、错误 JSON 不落成 WAV。尚未验证实际合成音色、速度或六个角色的声音区分。
