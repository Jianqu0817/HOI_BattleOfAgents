# 启动一场可回看的 AI 对局

当前主路线通过 `tools/native_campaign.py` 驱动原生 API；`Start-IRON.cmd` 将参数传给同一个 API 入口。原生默认国家为 GER/FRA/ITA/AUS/SWI，生成的观众页与审计记录写入本机 `runs/`。

## 当前主路线：原生 API CLI

### 初始化与桥接

在 Windows 克隆仓库后，从项目根目录运行共享初始化：

```powershell
python tools/setup.py
```

此步骤准备 Python 环境及共享地图/依赖。原生桥还需要 Visual Studio 2022 的 MSVC x64 C++ 工具和 Windows SDK，并使用 MinHook。请依照[原生 API 指南](NATIVE_API.md)获取固定依赖、构建桥接模块并加载到目标游戏进程；不要跳过原生构建直接启动 CLI。准备单人非铁人文本存档，载入并暂停游戏，确认参赛国原版 AI 已关闭。

### 开始与恢复

准备当前游戏进程 PID、存档目录及模型配置要求的密钥环境变量。根据[原生对局说明](NATIVE_CAMPAIGN.md)选择配置；示例配置为五国、每回合360小时、每国最多两个动作。首次运行：

```powershell
.venv/Scripts/python.exe tools/native_campaign.py --name native_show --pid <PID> --save-dir "<游戏存档目录>" --prompt-key --provider-check
```

继续同一场对局时保留名称、PID 与存档目录，使用恢复参数：

```powershell
.venv/Scripts/python.exe tools/native_campaign.py --name native_show --pid <PID> --save-dir "<游戏存档目录>" --resume --prompt-key
```

恢复会读取冻结配置及已记录响应。游戏进程重启、状态不匹配或执行回执未知时，先按原生对局说明核对，不要用新请求 ID 重放动作。模型密钥只通过隐蔽输入或进程环境变量提供。

### 离线审计与代码检查

对已完成或部分完成的本机对局运行离线审计；它不推进游戏、不调用模型：

```powershell
.venv/Scripts/python.exe tools/verify_native_campaign.py --name native_show
```

Python 离线检查命令如下（本指南只列出命令，不代表当前29回合对局跑过这一套检查）：

```powershell
.venv/Scripts/python.exe -m unittest discover -s tools -p "test_*.py" -v
.venv/Scripts/python.exe -I tools/parse_save.py --selftest
.venv/Scripts/python.exe -m compileall -q tools
```


原生接口已验证的范围和限制见[原生验收](NATIVE_ACCEPTANCE.md)。
