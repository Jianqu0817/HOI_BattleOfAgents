# 学院讲堂 · 大屏修订版

根据用户反馈，将舞台改为普通学院讲堂：白墙、浅木地板、实用灯具、自然采光和简洁侧边讲台。地图屏幕成为主体，取消金饰、雕花、拱柱和厚重前景。

本版使用 `gpt-image-2.5-4k`，原图均为 **3840×2160**。

- `backdrops/map_focus.png`：空讲堂后景，留有可替换的巨大屏幕。
- `foregrounds/side_lectern.png`：独立透明RGBA木讲台前景。
- `screen_masks/map_focus.png`：按实际生成图校准的屏幕内侧遮罩。
- `examples/map_presenter.png`：千问站在侧边的地图主导示例。
- `examples/lectern_presenter.png`：Claude在讲台后发言的示例。
- `examples/screen_only.png`：只嵌入地图、未放人物及讲台的示例。
- `layout.json`：屏幕区域、人物站位与实际显示面积比较。
- `prompts/`、`references/`、`generation.json`：生成提示、布局输入和文件校验值。

屏幕内侧为2851×1548像素，完整16:9游戏截图等比显示为2752×1548像素。相比上一版战况解说场景的完整地图显示区域，面积约为 **2.335倍**，文字和单位图标在相同画布上的显示尺寸也随之增大。示例的截图本身仍是此前保存的1280×720暂停画面，没有声称新增了原始游戏细节。

人物依据透明轮廓放在屏幕右侧，讲台也限制在右侧区域。原始角色图、表情与道具保持不变；此处只做静态构图。

在项目根目录重新合成：

```powershell
.venv/Scripts/python.exe tools/stage_preview.py --theme academy
.venv/Scripts/python.exe tools/stage_preview.py --theme academy --game-frame "C:/captures/hoi4.png"
```

叠加顺序为后景、屏幕内的真实游戏画面、人物、透明讲台前景。前景原图包含整张透明画布；示例先按透明轮廓取出讲台，再等比放入右侧空位，避免遮挡地图。

原白金议事厅版仍位于上一级目录。此次只做学院讲堂的大屏方案与配套前景，没有重新生成整套其他场景，也没有启动游戏、生成语音或制作视频。
