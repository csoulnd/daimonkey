# AI Coding 提效实践：网页演示第一版

10 屏，面向混合经验的开发者，预计 10–15 分钟。

- 演示入口：`/reports/ai-coding-efficiency/index.html`
- 报告厅详情：`/reports/ai-coding-efficiency`
- 阅读模式：`index.html?view=read`
- 完整讲稿：`script.html`，从 `source-snapshot.md` 生成。
- 内容来源：InferTechNote 的 `agent/reports/ai-coding-workflow-practice.md`，固定版本见 `source-manifest.json`。

演示支持左右键、PageUp/PageDown、首尾页、目录跳转、全屏及阅读模式。第 4 屏保留论文缩略图，可打开完整数据；第 5 屏可点击验收状态，第 10 屏可点击交付步骤。没有自动播放。

讲稿正文保留原文，省略 YAML 元数据和仓库维护用 Knowledge Extraction 检查项；图片沿用原稿。演示页以原文为基础，保留事实与实验口径，并根据主讲人的反馈补充定时任务、替换现场故事引子、调整讲述顺序；未补充新研究结论。

更新讲稿快照后，调整 `build-script.py` 中固定版本，使用站点虚拟环境解释器执行 `build-script.py`；讲稿由单份 Markdown 生成。演示摘要需要随内容变更同步检查。

网页文件使用原样 HTML 报告接口发布，不修改共享模板、其他报告或鉴权行为。

## 表情素材来源

后续添加表情时，在此登记文件、来源、作者、许可、修改情况与使用位置。演示页面不展示来源文字。

| 文件 | 表情 | 来源与署名 | 许可 | 修改情况 | 使用位置 |
|---|---|---|---|---|---|
| [assets/exploding-head.svg](assets/exploding-head.svg) | Exploding Head（U+1F92F，爆炸头） | [SVG 原图](https://openmoji.org/data/color/svg/1F92F.svg)；[OpenMoji，the open-source emoji and icon project](https://github.com/hfg-gmuend/openmoji) | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) | 未修改 | 第 1 屏 |

## 演示与讲稿对应（2026-10-13 调整）

| 页 | 演示重点 | 讲稿依据 / 补充 |
|---|---|---|
| 1 | 自我介绍与开场 | 原稿主题 + 主讲人开场 |
| 2 | 两层架构 | 原稿开篇 |
| 3 | 任务判断与 plan | 一、2 / 一、3 |
| 4 | 流程与人的上下文管理 | 一、3 / 一、4；论文图为辅助证据 |
| 5 | 精度乱码故事引子与真实验收 | 一、5；现场经历由主讲人展开 |
| 6 | skill + 定时任务 | 一、6 + 主讲人新增观点 |
| 7 | 生成与判别瓶颈 | 二、1 |
| 8 | 三类债务 | 二、4，提前作为切入点 |
| 9 | 偿还动作、练习与积累 | 二、2 / 二、3 / 二、4 |
| 10 | 积累与两个循环 | 原稿收尾 + 荀子《劝学》 |

补充引用：[OpenAI 官方 skill 编写指南](https://learn.chatgpt.com/docs/build-skills)；[《荀子·劝学》引文](https://www.lnpc.edu.cn/jcsjc/2024/0506/c269a24286/page.htm)。原稿讲稿快照保持当前版本，逐屏口播稿后续另行配套整理。
