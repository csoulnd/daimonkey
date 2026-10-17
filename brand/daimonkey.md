---
title: "daimonkey 大马猴 品牌标识"
type: reference
status: active
---

# daimonkey「大马猴」

以极简猴头为主体,圆耳与脸部的 m 形负空间呼应 monkey。头顶微微偏向一侧的翘毛打破对称,为简洁的几何轮廓增添灵气,表达好奇、聪明与探索精神。

| 资产 | 用途 |
|---|---|
| `static/assets/daimonkey.svg` | 单色 mark(currentColor 着色,导航/按钮/宠物) |
| `static/assets/daimonkey-color.svg` | 炫彩渐变版(强调场景) |
| `static/assets/favicon.svg` | 站点 favicon(深底炫彩) |
| `static/assets/daimonkey-{thinking,loading,happy,angry,calm,sleepy,wow,error}.svg` | 表情集单文件版(导出/文档用;站内用 base.html 内联 symbol `#i-monkey-*`) |

规范:mark 使用 `currentColor` 继承文字色;炫彩版仅用于 favicon、加载态等强调场景,不在正文重复出现;眼睛为负空间色(`--bg`)。

## 表情集(2026-10-17 扩展)

设计规则:**头型剪影永远不变**,只换五官——墨色(currentColor)五官画在负空间脸区内;嘴部开在口鼻区(muzzle)上,用负空间色(`class="mouth"`,站内由 CSS 按容器注入 `--panel`/`--bg`)。动效不做在五官里(`<use>` 阴影树无法从外部选中),而是容器 `data-face` 驱动整体动效(歪头/浮动/弹跳/摇头/呼吸),`prefers-reduced-motion` 下全部停用。

| symbol / 文件 | 表情 | 语义(建议映射) | 动效 |
|---|---|---|---|
| `#i-monkey` | 平静 | 默认/idle | 无 |
| `#i-monkey-thinking` | 思考(眼望上方+点) | 助手执行中 | 歪头 dm-think |
| `#i-monkey-loading` | 加载(旋涡眼) | 数据刷新/长任务 | 上下浮动 dm-bob |
| `#i-monkey-happy` | 开心(^ ^+大笑) | 完成/成功 | 弹跳 dm-pop |
| `#i-monkey-angry` | 生气(斜眉) | 限流/冲突(409/429) | 摇头 dm-shake |
| `#i-monkey-calm` | 冷静(闭目) | 空闲/呼吸态 | 呼吸 dm-breathe |
| `#i-monkey-sleepy` | 犯困(Zzz) | 长时间无操作 | 打盹 dm-drowse |
| `#i-monkey-wow` | 惊讶(圈眼+O嘴) | 突发大新闻/异常波动 | 后弹 dm-pop |
| `#i-monkey-error` | 出错(X 眼) | 请求失败/网络中断 | 摇头 dm-no |

已接线:assistant.js `petFace(name)` 在 startRun(→thinking)、成功(→happy)、失败/网络中断(→error)时切换宠物与面板标题图标。
