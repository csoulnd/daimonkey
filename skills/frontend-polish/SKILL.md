---
name: frontend-polish
description: 本站页面美化规范。适用场景：调整布局、字体、颜色、间距、新增页面或组件样式时。方法论源自 Open Props / Pico CSS 设计令牌体系 + Anthropic frontend-design skill 原则。
---

# 页面美化（frontend-polish）

## 本站前端架构约束（先读这个）

- **无构建链**：Jinja2 模板（`templates/`）+ 原生 JS（`static/assets/*.js`，IIFE 风格）+ 单一 CSS 文件（`static/assets/style.css`）。禁止引入 Node 构建、框架、npm 前端依赖。
- **改完必须重建**：`dist/` 是构建产物。模板/静态文件改动后执行
  `.venv/bin/python -m collector --build`（在 `/home/agent-heat-index` 下），浏览器才能看到。
- **资源指纹**：`_assets_version()` 对 `static/assets` 做哈希，`?v=` 自动失效缓存，新文件无需手动改版本号。
- **UI 语言为中文**，错误提示、按钮文案均用中文。

## 设计令牌体系（style.css 顶部 :root）

所有颜色/字阶/间距/圆角**只用令牌，不写裸值**：

| 类别 | 令牌 | 说明 |
|---|---|---|
| 颜色 | `--bg --panel --panel2 --ink --dim --line --line-strong --acc --heat --mom --up --down --code-bg --bubble-u` | 语义化；`--acc` 是唯一主强调色 |
| 字阶 | `--fs-xs --fs-sm --fs-base --fs-md --fs-lg --fs-xl` | 流体 clamp，勿用 px 硬编码字号 |
| 间距 | `--sp-1`~`--sp-7` | 4px 基准（4/8/12/16/24/32/48） |
| 圆角 | `--r-s --r-m --r-l --r-full` | 6/10/16/999 |
| 阴影 | `--shadow` | 悬浮层统一用这个 |

## 双主题规则

- 暗色是默认（`:root`），明色在 `html[data-theme="light"]` **只覆盖颜色令牌**——其它任何规则自动跟随，不要写 `[data-theme="light"] .xxx` 这种组件级覆盖。
- 新增颜色时必须同时在两个主题里定义。
- 切换由 `#btn-theme`（base.html）+ `app.js` 完成，预加载脚本在 `<head>` 防 FOUC；`color-scheme` 已随主题切换。
- 硬编码色值（`#xxxxxx`）出现在组件规则里视为 bug，改成令牌。

## 排版与布局原则

1. 正文字号 `var(--fs-base)`，行高 1.7（中文阅读），版心宽度约 920px（`--measure`）。
2. 间距节奏：组件内用 `--sp-2/--sp-3`，组件间用 `--sp-4/--sp-5`，区块间 `--sp-6+`。
3. 层级靠字号+颜色（`--ink/--dim`）表达，不滥用边框和分割线。
4. 按钮体系：主按钮 `--acc` 底 + `--acc-ink` 字；次按钮 `--panel2` 底 + `--line` 边；禁用态 `opacity:.55`。
5. 悬浮组件（如宠物面板）统一 `box-shadow: var(--shadow)` + `border-radius: var(--r-l)`。

## 改动清单（新页面/组件）

1. 模板：`templates/*.html`，extends base.html，用 `{% block content %}`；
2. 导航：base.html `.tree` 加链接（注意 └─ 树形符号归最后一个），app.js 高亮排除列表同步；
3. sitegen.py `build()` 加 `render(...)`；
4. JS：新文件放 `static/assets/`，IIFE + `"use strict"`，fetch 带 `{cache:"no-store"}`（动态）；
5. 重建 + 浏览器验证明暗两主题 + 窄屏（880px 断点）。

## 验证

- 明暗主题切换无 FOUC、无残留暗色块；
- `node --check` 通过改动的 JS；
- 页面 gzip 体积预算 ≤200KB（`scripts/verify.py` 会查）。
