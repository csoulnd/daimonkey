# AI Coding 提效实践：网页演示第一版

10 屏，面向混合经验的开发者，预计 10–15 分钟。

- 演示入口：`/reports/ai-coding-efficiency/index.html`
- 报告厅详情：`/reports/ai-coding-efficiency`
- 阅读模式：`index.html?view=read`
- 完整讲稿：`script.html`，从 `source-snapshot.md` 生成。
- 内容来源：InferTechNote 的 `agent/reports/ai-coding-workflow-practice.md`，固定版本见 `source-manifest.json`。

演示支持左右键、PageUp/PageDown、首尾页、目录跳转、全屏及阅读模式。第 4 屏可聚焦上下文长度，第 5 屏可点击验收状态，第 10 屏可点击交付步骤。没有自动播放。

讲稿正文保留原文，省略 YAML 元数据和仓库维护用 Knowledge Extraction 检查项；图片沿用原稿。演示页为原文提炼，保留事实与实验口径，未补充新研究结论。

更新讲稿快照后，调整 `build-script.py` 中固定版本，使用站点虚拟环境解释器执行 `build-script.py`；讲稿由单份 Markdown 生成。演示摘要需要随内容变更同步检查。

网页文件使用原样 HTML 报告接口发布，不修改共享模板、其他报告或鉴权行为。

## 表情素材来源

后续添加表情时，在此登记文件、来源、作者、许可、修改情况与使用位置。演示页面不展示来源文字。

| 文件 | 表情 | 来源与署名 | 许可 | 修改情况 | 使用位置 |
|---|---|---|---|---|---|
| [assets/exploding-head.svg](assets/exploding-head.svg) | Exploding Head（U+1F92F，爆炸头） | [SVG 原图](https://openmoji.org/data/color/svg/1F92F.svg)；[OpenMoji，the open-source emoji and icon project](https://github.com/hfg-gmuend/openmoji) | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) | 未修改 | 第 1 屏 |
