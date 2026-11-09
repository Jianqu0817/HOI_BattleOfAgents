# IRON · HOI Battle of Agents

让 **3–5 个独立 AI Agent** 在同一局《钢铁雄心4》中经营、谈判和下令，并为视频保留各国的**事前意图、对外表态、实际行动和结果**。

当前主路线使用 Windows 单进程游戏和原生 API 桥接。原生默认五国为 GER、FRA、ITA、AUS、SWI；各国实际执行模型由配置决定，角色品牌名称与模型供应商分开。

**[快速上手](docs/QUICKSTART.md) · [视频制作工作流](docs/VIDEO_WORKFLOW.md) · [节目设计](docs/PRODUCT_DIRECTION.md)**

[本地观察席与50回合录制入口](docs/OBSERVER.md)：查看实时进度、地图预览、公开互动、观众密档，记录笔记并导出观察战报。新录制配置将和平期推进设为约30天，仍保留事件中断与短时战争回合；五国模型分别配置，令牌从环境变量读取。

原生 Windows API 与实机验收说明：[原生 API](docs/NATIVE_API.md) · [验收记录](docs/NATIVE_ACCEPTANCE.md)。

原生模型对局入口是 `tools/native_campaign.py`，支持聊天、私信、密封决策、裁判、执行和素材导出。当前五国对局完成29/50回合；离线审计22,349通过、0失败、4 unknown。第30回合尚未提交，原定50回合尚未完成。详见[最新制作验收](docs/PRODUCTION_REVIEW_20261109.md)和[原生对局说明](docs/NATIVE_CAMPAIGN.md)。`Start-IRON.cmd` 菜单及 `tools/campaign.py` 仍是旧 UI 路线，不代表启动器已经接入原生 API。

当前角色品牌映射为 GER 白龙（gpt）、FRA 克莱德（claude）、ITA 基米（gemini）、AUS 大肥鱼（deepseek）、SWI 千问（qwen）；GLM 大狐狸为主持角色。品牌名与实际执行模型配置分离，主持模型不占用五个国家槽位。

项目保留叙事、角色、配音和剪辑接口及已有素材。当前语音生成与剪辑制作均暂缓；六个声线的模型与参考 WAV 为本机资产且由 Git 忽略。宣战和海空军任务设置已有独立验收；`news.66` 在第9回合验证过安全暂停、选项选择、resolved 关窗和恢复剩余12小时，其他通用事件未验证。配音和剪辑边界见[制作工作流](docs/NARRATIVE_PRODUCTION.md)、[视频工作流](docs/VIDEO_WORKFLOW.md)及[GPT-SoVITS](docs/GPT_SOVITS.md)。

## 角色立绘

六位角色各有 **2种姿势 × 5个表情 × 普通/说话两帧，共120张** 透明全身PNG，保留猫猫拳、抱书、折扇、记事本等专属动作与道具。每组仅嘴部发生变化，供说话时交替显示。实际图片为1024×1536 RGBA，按指定的 `gpt-image-2.5-2k` 模型标识生成。

| 基米：猫猫拳 | 克莱德：抱书歪头 |
| --- | --- |
| <img src="assets/characters/poses/gemini/cat_paws/neutral.png" width="240" alt="基米猫猫拳立绘"> | <img src="assets/characters/poses/claude/book_hold/neutral.png" width="240" alt="克莱德抱书立绘"> |

[完整立绘说明](docs/CHARACTER_ART.md) · [图片与生成清单](assets/characters/poses/)；下载后可打开该目录的 `index.html` 查看全部姿势及表情。角色图更新不恢复剪辑或语音制作。

## 4K舞台素材

当前优先使用[学院讲堂大屏版](assets/stage/academy/README.md)：普通白墙、浅木地板、简洁讲台，地图显示面积约为上一版战况解说场景的2.3倍。另保留原议事厅/战况解说/单人发言后景、透明讲台前景，以及发言者灯光、屏幕遮罩与静态组合示例。舞台模型生成原图均为3840×2160，地图使用真实游戏截图嵌入实体屏幕；后景不烘焙角色或游戏信息。

[分层说明与坐标](assets/stage/README.md) · [舞台素材目录](assets/stage/)；下载后打开该目录 `index.html` 浏览原图及示例。当前仍只制作静态素材，未恢复视频剪辑、语音生成或对局运行。

## 文档导航

- [快速上手](docs/QUICKSTART.md)：原生 CLI 初始化、开始／恢复和离线审计；另有旧 UI 操作说明。
- [原生 API 构建与验收边界](docs/NATIVE_API.md) · [原生对局入口](docs/NATIVE_CAMPAIGN.md) · [最新制作验收](docs/PRODUCTION_REVIEW_20261109.md)
- [角色与配置](docs/CHARACTERS.md) · [叙事与制作接口](docs/NARRATIVE_PRODUCTION.md) · [视频工作流](docs/VIDEO_WORKFLOW.md) · [技术路线（含历史阶段）](docs/TECHNICAL_ROADMAP.md)
- [第三方声明](NOTICE.md)

## 启动

```powershell
git clone https://github.com/Jianqu0817/HOI_BattleOfAgents.git
cd HOI_BattleOfAgents
python tools/setup.py
```

`tools/setup.py` 初始化 Python 环境、共享地图与依赖。原生 API 还需要 MSVC x64 C++ 工具和 MinHook；按[原生 API 指南](docs/NATIVE_API.md)先准备依赖并构建、加载桥接模块。`Start-IRON.cmd` 的菜单仍对应旧 UI 路线，并未接入原生对局入口。请准备备份的单人非铁人存档并暂停。

已验证：Windows、HOI4 1.19.4.2、简体中文、1280×720 游戏渲染。其他版本、语言、UI 比例和替换地图需要重新验收。安装器获取固定版本后端，不分发游戏本体。

仓库发布项目源码、共享配置和文档。本地 `runs/`、`exports/`、个人路径配置、模型密钥、游戏存档、模型权重、参考音频和录制素材不纳入 Git。密钥通过环境变量提供，Windows支持读取当前用户持久环境变量；配置只记录变量名。

## 一回合如何运行

```text
暂停并采集同一时刻观察
 → 各国事前私密意图摘要
 → 两轮公开讨论
 → 定向私信及回信
 → 密封决定与独立裁判审查
 → 串行普通操作与存档核验
 → 共同推进并暂停
 → 本国复盘与经验积累
 → 观众专属解说、回看与素材导出
```

玩家只能看到本国观察和本人参与的私信。全知解说不回流玩家。“私密意图”是模型事前提交的简短摘要，不是隐藏思维链。承诺保留逐字原话、对象、条件与期限；聊天不会自动变成游戏条约、贸易或援助。

新建原生对局将观众专属采访和导演点评延后到赛后；两轮公开讨论、私聊和公开主持仍在赛中运行。赛后使用 `python tools/native_campaign.py --name native_show --postmatch --prompt-key` 补齐观众内容，无需启动游戏。已有冻结对局保持原配置，详见[原生对局说明](docs/NATIVE_CAMPAIGN.md)。

## 可用功能

- 原生 CLI 管理冻结配置、分阶段恢复、同步沟通、密封决定、裁判审查、动作执行与共同时间。
- 已有宣战和海空军任务设置的独立验收；海空任务设置不表示战斗胜利，通用事件弹窗处理尚未验证。
- 离线 HTML 回看、CSV/JSONL、SRT、旁白与镜头草稿等素材接口；语音生成和剪辑制作当前暂缓。
- 旧 UI 路线仍保留科研、国策、生产等经营功能说明；这些不能据此视为当前原生 API 对局的已验收功能，见[快速上手](docs/QUICKSTART.md)旧 UI 章节。

字幕使用建议剪辑时间，须在剪辑软件中与 OBS 等录屏对齐。观众素材含全部秘密，不能作为玩家输入。

## 运行示例

原生模型对局（按原生 API 文档完成构建和加载；游戏暂停、参赛国原版 AI 已关闭）：

```powershell
.venv/Scripts/python.exe tools/native_campaign.py --name native_show --pid YOUR_PID --save-dir "YOUR_SAVE_DIRECTORY" --prompt-key
.venv/Scripts/python.exe tools/native_campaign.py --name native_show --pid YOUR_PID --save-dir "YOUR_SAVE_DIRECTORY" --resume --prompt-key
```

旧 UI 经营路线（`Start-IRON.cmd` / `tools/campaign.py`，与原生 API 主路线分开）：

```powershell
.venv/Scripts/python.exe tools/campaign.py doctor --config configs/campaign.quick.example.json
# 先在启动器中输入密钥，或设置模型文件指定的进程环境变量。
.venv/Scripts/python.exe tools/campaign.py run --name first_show --config configs/campaign.quick.example.json --turns 1
.venv/Scripts/python.exe tools/campaign.py resume --name first_show
```

离线核对原生对局：

```powershell
python tools/verify_native_campaign.py --name native_show
```

该审计只检查指定本地对局证据，不推进游戏或调用模型。详见[快速上手](docs/QUICKSTART.md)。

## 验证与范围

当前原生对局范围：29/50回合，离线审计22,349通过、0失败、4 unknown；unknown 与未到冻结终点及第30回合未提交有关。宣战和海空军任务设置有独立验证，任务设置不代表战斗胜利。另有单次 `news.66` 安全暂停、选择、resolved 关窗并恢复12小时的事件闭环。OBS 已验证1280×720、30fps、约2秒暂停画面、无音轨；不是历史对局录像。Shotcut 工程导出仍属实验性，软件内发现的时长异常待后续修复；剪辑与成片制作暂缓。详见[制作验收记录](docs/PRODUCTION_REVIEW_20261109.md)。

当前对局依赖指定游戏构建与已加载的原生桥。其他构建、地图、Mod 和未逐项验证的机制不能由本次结果外推。通用弹窗处理尚未验证；海空任务设置不等同海空战果。项目自定义计分与原版投降胜负分别记录。社区地图须先做兼容验收。

## 开发检查

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tools -p 'test_*.py' -v
python -I tools/parse_save.py --selftest
```

本机335项离线测试通过，存档解析自测通过；此前还完成了Python模块编译以及两个事件sidecar的隔离构建/自测。离线测试不需要游戏或 API 密钥。GitHub Actions 在 Windows/Ubuntu 运行离线检查；界面操作单独在 Windows 游戏内验收。

[场景与计分](docs/SCENARIOS.md) · [陆军校准](docs/ARMY_CONTROL.md) · [模型接口](docs/MODEL_INTERFACE.md) · [技术路线](docs/TECHNICAL_ROADMAP.md) · [第三方声明](NOTICE.md)
