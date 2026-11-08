# 第三方组件

IRON 的 Windows 控制验证使用 [HOI4 Live Tools](https://github.com/klimPaskov/HOI4-Live-Tools)，固定源码版本 `f644f9a0c8f86c8d8d635687b93116437870e085`。Copyright 2026 Klim Pashkov，采用 Apache License 2.0；原许可与声明保存在 `third_party/`。

`tools/live_compat.py` 的客户区截图适配参考该项目的 Windows capture、input 和 screen 模块；依赖保存在独立目录，未修改上游源码。

HOI4 Live Tools 的早期路线研究参考 smerss 的 [Clausewitz MCP](https://github.com/smerss/Clausewitz-MCP)。当前主路线使用 Windows 原生 API 桥接；`Start-IRON.cmd` 与 `tools/campaign.py` 保留为旧 UI 路线。

本项目本地 GPT-SoVITS 后端代码固定到 [RVC-Boss/GPT-SoVITS commit `04a44eb977586645f33594b72af13a035a2056c6`](https://github.com/RVC-Boss/GPT-SoVITS/tree/04a44eb977586645f33594b72af13a035a2056c6)，采用 MIT 许可证。IRON 安装说明记录了预训练模型及工具的来源和 revision；相关模型、声线权重、参考音频及其他第三方权重不随 IRON 仓库分发，各自许可见原提供方。
