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
| `static/assets/daimonkey/{表情名}.svg` | 表情集单文件版(17 个,含两批评审修订;站内用 base.html 内联 symbol `#i-monkey-*`) |

规范:mark 使用 `currentColor` 继承文字色;炫彩版仅用于 favicon、加载态等强调场景,不在正文重复出现;眼睛为负空间色(`--bg`)。

## 表情集(2026-10-17 扩展)

设计规则:**头型剪影永远不变**,只换五官——墨色(currentColor)五官画在负空间脸区内;嘴部开在口鼻区(muzzle)上,用负空间色(`class="mouth"`,站内由 CSS 按容器注入 `--panel`/`--bg`)。动效不做在五官里(`<use>` 阴影树无法从外部选中),而是容器 `data-face` 驱动整体动效(歪头/浮动/弹跳/摇头/呼吸),`prefers-reduced-motion` 下全部停用。

| symbol / 文件 | 表情 | 语义(建议映射) | 动效 |
|---|---|---|---|
| `#i-monkey` | 平静 | 默认/idle | 无 |
| `#i-monkey-thinking` | 思考(眼望上方+点) | 助手执行中 | 歪头 dm-think |
| `#i-monkey-loading` | 加载(旋涡眼) | 数据刷新/长任务 | 上下浮动 dm-bob |
| `#i-monkey-happy` | 开心(^ ^+大笑) | 完成/成功 | 弹跳 dm-pop |
| `#i-monkey-angry` | 生气(斜眉+头顶小火苗) | 限流/冲突(409/429) | 摇头 dm-shake |
| `#i-monkey-calm` | 冷静闭目,猴子自己当木鱼(头顶开槽+木槌敲头) | 空闲/呼吸态 | 呼吸 dm-breathe |
| `#i-monkey-sleepy` | 犯困(Zzz) | 长时间无操作 | 打盹 dm-drowse |
| `#i-monkey-wow` | 惊讶(圈眼+O嘴) | 突发大新闻/异常波动 | 后弹 dm-pop |
| `#i-monkey-error` | 出错(X 眼) | 请求失败/网络中断 | 摇头 dm-no |

第二批图鉴扩展(2026-10-17,共 9 个,暂未接线动效,应用为下一步;同日评审修订见括注):

**情绪专属色规范(2026-10-17 评审定)**:头型与主五官保持 `currentColor` 跟随主题;情绪签名单独用固定色——怒火渐变 `#ffd24d→#ff6b4a`(sprite 内 `dm-fire`)、爱心 `#ff6b9d`、腮红 `#ff8fa8`、彩纸四色 `#7c8cff/#4fd6c4/#f0b429/#ff6b9d`、木鱼 `#c98a4b`+`#8a5a33`。loading 双眼为 3/4 圆弧 + SMIL 自转(1.1s/圈,独立文件同样生效)。

| symbol / 文件 | 表情 | 语义(建议映射) |
|---|---|---|
| `#i-monkey-laugh` | 大笑(眯眼+泪花+张嘴) | 数据大涨/破纪录 |
| `#i-monkey-sad` | 难过(垂眉+单泪) | 数据回落/连败 |
| `#i-monkey-cry` | 大哭(闭眼+双泪柱) | 大跌/告警 |
| `#i-monkey-shy` | 害羞(瞇眼+红晕) | 被表扬/收藏 |
| `#i-monkey-cool` | 墨镜酷 | 榜首/稳态领先 |
| `#i-monkey-wink` | 眨眼吐舌 | 调皮提示/彩蛋 |
| `#i-monkey-love` | 爱心眼 | 收藏/点赞 |
| `#i-monkey-dizzy` | 头晕(波浪眼+星星) | 刷新中/重载 |
| `#i-monkey-party` | 庆祝(尖帽+彩纸) | 里程碑/上线 |

已接线:assistant.js `petFace(name)` 在 startRun(→thinking)、成功(→happy)、失败/网络中断(→error)时切换宠物与面板标题图标。

## 静态位应用(2026-10-17 扩展)

| 位置 | 表情/形式 | 模板 |
|---|---|---|
| AI 摘要卡徽标「马猴说」 | 平静 mark(16px,--acc 着色) | `_macros.html`(榜单/新闻/产品三页)、`news_digest.html` |
| 数据过期横幅 | 惊讶 wow(替代 ⚠) | `base.html` stale-banner |
| 页脚署名「大马猴 · 方法论 · 数据源」 | 平静 mark(15px) | `base.html` footer |
| 登录卡欢迎位 | 冷静 calm(76px,呼吸动效) | `login.html` |
| 顶栏用户头像 | 炫彩猴圆标:平静/开心/生气/戴帽子(`#i-ava-*`,深底 #0a0a10+品牌渐变,与 favicon 同视觉) | `base.html` sprite + `assistant.js` |

头像规范:默认平静;菜单「换个表情」按 平静→开心→生气→戴帽子 轮换,存 `localStorage.dm-avatar`;防闪烁内联脚本(base.html)用白名单读同一键。五官为负空间 #0a0a10(与圆底同色,天然镂空)。

规范:静态位一律用内联 symbol(不加载独立 svg 文件);负空间五官(.eye/.mf/.ms)由所在容器的 CSS 注入背景色;表情动效仅用于状态反馈,静态品牌位不加动效(登录页呼吸除外)。
