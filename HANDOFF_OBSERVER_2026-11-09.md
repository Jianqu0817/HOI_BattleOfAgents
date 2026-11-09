# 观察席、月度和平回合与六模型接入

## 当前状态

- 旧 `native_show_50_v2` 保持29/50；未重放命令。
- 新轮计划为 `native_recording_50_20261109`，从旧局第29回合存档开始新的50回合；目录目前仅有准备资料和之前的原生API就绪检查，尚无 `manifest.json`、参赛决策或实际新回合。
- 用户此前暂停对局，后续授权调整节奏、保存令牌与测试新API；没有重新启动50回合或录像。
- 最近窗口检查：旧HOI4进程46912已退出；OBS58132仍在但无可见窗口；没有可见的观察面板浏览器窗口。不要把该旧PID当作可用进程。
- 本地观察服务 `tools/observer.py` 已在127.0.0.1:8765启动（启动时PID29652，恢复工作时核实）。

## 已实现

1. 观察席：真实OBS游戏预览、回合/阶段/耗时、五国公开消息、公开局势时间线、观众密档、人工笔记、Markdown战报；观众资料不进入玩家上下文。
2. 录制入口：`tools/record_session.py`使用OBS WebSocket和已有IRON_VERIFY配置。录像与预览分别保存；每轮开始前检查录像状态，正常完成或运行出错时停止录像。当前没有执行该入口。
3. 基础知识：Luna总结、主Agent校准的 `configs/prompts/hoi4_basics.md` 通过 `show.knowledge_file` 加入五国提示，来源身份/覆盖范围见 `docs/HOI4_KNOWLEDGE_SOURCES.md`。
4. 和平预算720小时（约30天）、战争24小时、关键事件6小时；上层720小时仍按至多6小时的小段推进并检查事件。已冻结旧局不改配置。
5. 无新增公开事件时，在现有第二轮公开讨论加入短句关心、调侃或回怼，不新增模型调用，不强制改变策略；允许自愿说辞，系统不自动公开私信或密封决定。
6. 六个令牌已保存并核对到Windows当前用户环境，模型客户端及原生入口支持从持久环境加载；桌面原令牌文件可由用户删除。没有将值写入代码、配置或Git。进程环境中的显式覆盖优先。

## 新模型配置

`configs/campaign.recording50.json`：德国GPT、法国Claude、意大利Gemini、奥地利DeepSeek、瑞士Qwen，GLM负责主持/裁判/赛后导演。每个角色独立环境变量和模型配置：`configs/model.cast.*.json`。

最小接口验证记录 `runs/api_cast_probe_20261109.json`（不上传）保留初次诊断。后续用户确认Claude渠道已调整，项目客户端复验 `runs/api_claude_glm_project_client_20261109.json` 中GLM和Claude均已返回合法ready JSON（约1.91秒/2.19秒），加此前Qwen、DeepSeek、GPT、Gemini，共六个配置已通过最小连通性验证，未替换模型。GLM仅关闭可选json_object请求参数、仍严格校验最终JSON；Claude改用原生Anthropic Messages协议、顶层system、保留high effort并去掉该模型明确拒绝的temperature。协议/参数进入缓存与审计指纹。仅验证接口连通性，尚未跑新局。

模型客户端遇到明确 `model_not_found` 时，即使HTTP状态为503也不会自动重试，避免对同一永久错误重复付费/等待。

## 恢复前

接口阻碍已解决，按用户新指令启动。需要重新打开游戏、载入指定存档、加载对应原生桥并确认五国AI关闭；新进程不能重用旧PID或旧请求身份。收起无用游戏面板、最小化OBS和观察浏览器，使用game_capture；有待选择的事件保留信息，不随意关闭。

操作说明与入口见 `docs/OBSERVER.md`、`docs/NATIVE_CAMPAIGN.md`。语音仍留待之后服务器生成，不制作剪辑成片。
