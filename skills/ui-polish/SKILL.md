---
name: ui-polish
description: 本站 UI 美化专家。适用场景:页面视觉与布局优化、设计令牌、图标系统、favicon、导航重组、截图审查与多模态美化循环、新增页面样式。
---

# UI 美化专家(ui-polish)

前身:frontend-polish(2026-10-16 升级并入,规范全部保留)。本专家 = 原设计令牌规范 + 多模态截图美化循环。

## 本站前端架构约束(先读这个)

- **无构建链**:Jinja2 模板(`templates/`)+ 原生 JS(`static/assets/*.js`,IIFE 风格)+ 单一 CSS 文件(`static/assets/style.css`)。禁止引入 Node 构建、框架、npm 前端依赖。
- **改完必须重建**:`dist/` 是构建产物。模板/静态文件改动后执行
  `.venv/bin/python -m collector --build`(在仓库根目录下),浏览器才能看到。
- **资源指纹**:`_assets_version()` 对 `static/assets` 做哈希,`?v=` 自动失效缓存,新文件无需手动改版本号。
- **UI 语言为中文**,错误提示、按钮文案均用中文。

## 设计令牌体系(style.css 顶部 :root)

所有颜色/字阶/间距/圆角**只用令牌,不写裸值**:

| 类别 | 令牌 | 说明 |
|---|---|---|
| 颜色 | `--bg --panel --panel2 --ink --dim --line --line-strong --acc --heat --mom --up --down --code-bg --bubble-u` | 语义化;`--acc` 是唯一主强调色 |
| 字阶 | `--fs-xs --fs-sm --fs-base --fs-md --fs-lg --fs-xl` | 流体 clamp,勿用 px 硬编码字号 |
| 间距 | `--sp-1`~`--sp-7` | 4px 基准(4/8/12/16/24/32/48) |
| 圆角 | `--r-s --r-m --r-l --r-full` | 6/10/16/999 |
| 阴影 | `--shadow` | 悬浮层统一用这个 |

## 双主题规则

- 暗色是默认(`:root`),明色在 `html[data-theme="light"]` **只覆盖颜色令牌**——其它任何规则自动跟随,不要写 `[data-theme="light"] .xxx` 这种组件级覆盖。
- 新增颜色时必须同时在两个主题里定义。
- 切换由 `#btn-theme`(base.html)+ app.js 完成,预加载脚本在 `<head>` 防 FOUC;`color-scheme` 已随主题切换。
- 硬编码色值(`#xxxxxx`)出现在组件规则里视为 bug,改成令牌。

## 排版与布局原则

1. 正文字号 `var(--fs-base)`,行高 1.7(中文阅读),版心宽度约 920px(`--measure`)。
2. 间距节奏:组件内用 `--sp-2/--sp-3`,组件间用 `--sp-4/--sp-5`,区块间 `--sp-6+`。
3. 层级靠字号+颜色(`--ink/--dim`)表达,不滥用边框和分割线。
4. 按钮体系:主按钮 `--acc` 底 + `--acc-ink` 字;次按钮 `--panel2` 底 + `--line` 边;禁用态 `opacity:.55`。
5. 悬浮组件(如宠物面板)统一 `box-shadow: var(--shadow)` + `border-radius: var(--r-l)`。

## 多模态截图美化循环(本专家的核心工作流)

对目标页面执行「截图 → 分析 → 修改 → 重建 → 再截图」,直到视觉达标:

1. **截图**:浏览器工具打开页面(明暗两主题、880px 窄屏各一张),全页截图。
2. **分析**:把截图交给多模态模型,按固定审查单提问——层级是否清晰、间距节奏、对齐、色彩一致性(是否只用令牌色)、图标/文字混排、空态与加载态、中文排版(标点悬挂、行宽)。
3. **修改**:只改令牌与模板/JS,遵循上文约束;一次循环只处理一类问题(先布局后色彩后细节)。
4. **重建**:`--build` 后复截对比。
5. **沉淀**:达标后把「改了什么、为什么」追加到本文件决策记录;发现新的通用规则补进审查单。

## 图标系统规范

- 图标一律**内联 SVG**(symbol + use 或直接内联),禁止图标字体与外部图标 CDN;`currentColor` 继承文字色,`aria-hidden="true"` + 文本兜底。
- 尺寸走 `--fs-*` 或固定 16/20px 两档;favicon 同源生成(svg → 多尺寸)。

## 改动清单(新页面/组件)

1. 模板:`templates/*.html`,extends base.html,用 `{% block content %}`;
2. 导航:base.html `.tree` 加链接(注意 └─ 树形符号归最后一个),app.js 高亮排除列表同步;
3. sitegen.py `build()` 加 `render(...)`;
4. JS:新文件放 `static/assets/`,IIFE + `"use strict"`,fetch 带 `{cache:"no-store"}`(动态);
5. 重建 + 走一遍多模态截图循环(明暗两主题 + 窄屏)。

## 验证

- 明暗主题切换无 FOUC、无残留暗色块;
- `node --check` 通过改动的 JS;
- 页面 gzip 体积预算 ≤200KB(`scripts/verify.py` 会查);
- 多模态循环最后一轮截图无高/中级别问题。

## 决策记录

- 2026-10-19(云剪贴板页,主责 workspace 专家,此处仅备注):新增 `/clipboard` 页(文本+文件两区、配额条、拖拽上传)按本专家清单落地——`i-clip` 内联图标、style.css 追加令牌化云剪贴板段(仅 `:root` 令牌,无裸值)、`clipboard.js` IIFE+defer、app.js 导航高亮排除表加 `/clipboard`、base.html 顶栏/页脚/防闪烁均加 clipboard_page 分支。

- 2026-10-17(P6/P1 收尾):榜单页条数统一(联合10/模型20/其余前15),观察区改 `<details>` 折叠;侧栏收起改 220ms 宽度过渡(桌面,min-width:881,移动端布局不受影响);工作台入口防闪烁=localStorage+head 预载 html.authed 类(与主题防 FOUC 同模式),探针负结果自动清除标记。

- 2026-10-16:frontend-polish 升级为 ui-polish 专家,新增多模态截图循环与图标系统规范。
- 2026-10-16(方向定案):需求方选定 **A「极夜终端」**,暗/亮双主题=「A 结构+双色板」而非双布局;无悔改进(图标导航/favicon/令牌化/meta)同日落地。
- 2026-10-17(A 方向二期 + 六点需求):① 站点更名「InferTech 观察站」(需求方重心转向推理技术;registry `site.name` 一行可改,品牌位全部走 `{{ site.name }}`);② 导航「AI Agent 新闻」→「AI 新闻」;③ 新增独立登录页 `/login`(渐变卡片,?next= 回跳)+ 侧栏「登录」入口(assistant.js 登录态联动显隐);④ 方法论撤独立页,并入首页底部 `<details id="methodology">` 折叠块(全站链接改锚点);⑤ 首页主表改 8 列(综合/算法/AI 复评),去掉 Stars/npm 列;⑥ `.score.final` 与 `.method-fold`/`.login-card` 组件入令牌体系。验证:110 测试 + verify 33/33。
- 2026-10-17(v1.5 榜单呈现,与 data-update 专家共担):联合榜展示层变换 `_unified_display`(sitegen):剔除 status=stale → final=算法×70%+AI×30%(registry `ai.scoring.ai_weight` 可调) → 重排名 → 前 10;榜单 AI 总结新增 page=board(指纹=快照日+前15名次+AI分数量);export API 契约不动(仍算法分全量)。

- 2026-10-21(助手面板重构)：助手按「会话 / 模式 / 专家·Skill」三区重组（左侧会话列表、顶部对话/运维模式、右侧对话与专家 popover，≤720px 会话收纳为抽屉）；尺寸改成 CSS `clamp()/dvh` 自适应、JS 只持久化拖拽后的像素（`dm-asst-size`，双击左上角恢复默认），窄面板 <380px 自动收起副标题；未登录由 `html.authed` 整体隐藏宠物/面板/工作台入口，面板内不再渲染登录表单，`requireAuth` 只做防御拦截。换表情 bug 的根因不是样式：① 顶栏头像存在两处渲染方（base.html 内联脚本 + assistant.js），② 异步 `loadSessionMeta` 探针会二次渲染覆盖切换结果，③ 旧实现只改顶栏、宠物与面板标题仍用旧脸，④ 每次进入重复绑监听造成泄漏。修复为单一 `applyAvatar()` 同时刷新顶栏 `#i-ava-*`、悬浮宠物、面板标题与文字标签，删除内联重复渲染与重复绑定，监听只在初始化时挂一次。
- 2026-10-22(图标系统 v2)：确立 33 个 symbol 体系 = 7 导航（重绘）+ 19 操作 + 4 脸 + 3 头像，统一 24×24 viewBox、1.6px 描边、`currentColor` 继承文字色；28 处文字字形替换为图标；删除 16 个孤儿 symbol 后 sprite raw 28,855→14,966 B（gzip −40%）。理由是字形图标基线随字体/平台漂移且无法随主题换色，symbol 才能一处定义、全站复用。
- 2026-10-22(助手三脸选择器与头像配色)：移除戴帽子脸，保留普通/开心/生气三脸。浅色主题五官“消失”的根因**不是对比度**，而是五官 `#0a0a10` 与深色脸洞**同色**；修复为五官 `#f6f2ff` + 独立渐变，不再依赖脸洞颜色。`.avatar` 背景改 `var(--bg)` 随主题（原先写死深色底，浅色主题下变成一块暗斑）。login/404 是独立页、不 extends base，需各自内联 mini-sprite，否则图标缺失。
