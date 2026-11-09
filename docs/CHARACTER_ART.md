# 多姿势角色立绘

角色库位于 `assets/characters/poses/`。六人各2种姿势，每种5个表情，每个表情有普通/说话两帧，合计 **120张全身透明PNG、60组配对**。使用用户提供的参考图，按指定的 `gpt-image-2.5-2k` 模型标识调用图像编辑API；本批接口实际返回尺寸为 **1024×1536**，格式为RGBA PNG。

| 角色 | 姿势一 | 姿势二 | 保留的身份特征 |
| --- | --- | --- | --- |
| 基米 / Gemini | 猫猫拳 | 指尖点脸、活泼招呼 | 猫耳尾、异色瞳、星饰与星链 |
| 克莱德 / Claude | 双手抱书、轻微歪头 | 展开书本、抬眼回应 | 花饰、缎带、书本与端庄长裙 |
| 千问 / Qwen | 展扇致意 | 持扇思索 | 折扇、绣花手袋、长辫与小帽 |
| 大肥鱼 / DeepSeek | 提裙招呼 | 记事本与蓝色笔 | 鲸耳、鲸尾、女仆装与鲸鱼围裙 |
| 大狐狸 / GLM | 抱书托腮 | 展书摊掌解说 | 狐耳尾、铃铛、头顶眼罩与Z书本 |
| 白色龙娘酱 / GPT | 托掌展示小云团 | 小云团伴随的招呼 | 龙角翼尾、盘结、银链与小云团 |

整体使用明亮轻快的赛璐璐风格，少女面部比例，表情以友善、惊讶、思考、轻微不满与害羞等为主。不同角色各有适合其性格的五个表情；完整枚举见生成清单。

## 浏览与使用

下载仓库后，在本机打开 `assets/characters/poses/index.html`。页面可切换棋盘格、浅色及深色底，点击图片下载原始PNG；这些预览底色没有写入PNG。

- `角色ID/姿势ID/表情ID.png`：独立全身立绘。
- `contact_sheet.jpg`：60张图的总览。
- `references/`：用户提供的服装、身份、动作与道具参考；原图有背景不影响生成图的透明要求。
- `prompts/` 与 `generation_plan.json`：每张图的提示词、输入图片和参数。
- `generation.json`：文件SHA256、实际尺寸、透明通道检查及生成来源。
- `verification.json`：六人、12种姿势、120个独立文件及60组口型配对的完整性检查。

## 说话口型配对

每张 `表情ID.png` 对应一张 `表情ID_talk.png`。例如 `gemini/cat_paws/neutral.png` 与 `neutral_talk.png` 可以交替显示。画廊提供“说话预览”按钮，默认每170毫秒切换一次，点击停止后恢复普通帧；也可以单独下载说话差分。

口型通过同一模型的局部遮罩编辑生成，再由 `tools/talking_sprite.py` 仅融合嘴部区域。整张图的透明通道、嘴部矩形之外的像素与原图完全一致；每一组都做像素比较，避免身体、发饰和服装闪动。已张嘴表情使用另一种说话口型，不把配对文件名当作严格的张嘴/闭嘴标签。

`talking_pairs.json` 记录两帧路径、嘴部范围、差异像素数和SHA256；`talking_mouths_contact_sheet.jpg` 是口型对比总览。角色配置中的 `speaking_portraits` 提供与普通 `portraits` 对应的说话图片。`talk_prompts/` 和 `mouth_masks/` 保留口型生成输入。

这是两帧口型动画，不是按音素生成的唇形同步。实际播放应在说话时交替、停话时恢复普通帧；本轮只提供图片及预览，不生成语音。

`configs/characters.authored.json` 的 `default_pose` 指定默认姿势，`poses` 保存各姿势的表情路径，既有 `portraits` 字段继续提供兼容的默认表情映射。旧的单姿势图片和 `characters.example.json` 保留，避免破坏历史记录。

## 生成方式

先为每人生成两张中性表情的姿势底图，再以对应底图作为首要参考逐张生成其他表情。表情差分提示只改变眼眉、嘴型和轻微脸红，约束身体、服装、道具和构图保持一致。并发上限为4；复用已成功文件，失败不自动重跑整批。

本次复用Codex `imagegen` Skill 自带的 `image_gen.py edit`，没有修改Skill脚本。需要复现时安装可选的 `openai` Python包，通过 `OPENAI_BASE_URL` 配置提供商的 `/v1` 地址，密钥仅放进程环境变量 `OPENAI_API_KEY`。例如在项目根目录执行一次表情编辑：

```powershell
.venv/Scripts/python.exe "$env:USERPROFILE/.codex/skills/.system/imagegen/scripts/image_gen.py" edit `
  --model gpt-image-2.5-2k `
  --image assets/characters/poses/gemini/cat_paws/neutral.png `
  --image assets/characters/poses/references/gemini_1.webp `
  --prompt-file assets/characters/poses/prompts/gemini-cat_paws-excited.txt `
  --size 1024x1536 --quality high --background transparent --output-format png `
  --no-augment --out runs/art_trial.png
```

不同服务商的同名模型和透明背景能力需要单独核实。本批文件检查的是实际RGBA透明像素，不把白色或棋盘格背景当作透明。生成模型只约束姿势一致性，不保证表情文件之间逐像素完全相同。

本轮只更新角色图片与配置，不恢复剪辑、录屏或语音生成。

## 制作待办

- [ ] 重绘基米（Gemini）部分说话差分的嘴型。先把 `cat_paws` 和 `playful_greeting` 的闭嘴、说话帧逐对并排，标出不自然的表情；以对应闭嘴原画为基准重绘，保留脸型、视线和手势。用一段正常对话及一声“喵”试播，确认张合自然后再替换现有立绘。
