# 第三方组件

IRON 的 Windows 控制验证使用 [HOI4 Live Tools](https://github.com/klimPaskov/HOI4-Live-Tools)，固定源码版本 `f644f9a0c8f86c8d8d635687b93116437870e085`。Copyright 2026 Klim Pashkov，采用 Apache License 2.0；原许可与声明保存在 `third_party/`。

`tools/live_compat.py` 的客户区截图适配参考该项目的 Windows capture、input 和 screen 模块；依赖保存在独立目录，未修改上游源码。

HOI4 Live Tools 的原生运行路线参考 smerss 的 [Clausewitz MCP](https://github.com/smerss/Clausewitz-MCP)。IRON 当前验证使用 Windows Python 界面与控制台路线。
