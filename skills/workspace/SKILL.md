---
name: workspace
description: 个人工作台专家。适用场景:登录后的工作台分区(报告厅/个人知识库/后续新功能)、用户资源隔离、用户自配 GitHub 知识库仓库。
---

# 个人工作台专家(workspace)

## 需求方对齐(2026-10-17)

- 侧栏「个人工作台」分组(报告厅、个人知识库),**未登录连分组都不显示**(服务端另有 401 门禁)。
- **用户隔离**:知识库每个账号可配置自己的 GitHub 仓库,默认 InferTechNote(`csoulnd/InferTechNote`)。
  已落地:`users.knowledge_repo` 列 + `userctl repo <用户名> <url|->` + `users.get/set_knowledge_repo`。
- 登录入口在**右上角「登录」**(/login 页)。
- 品牌统一 daimonkey(见 brand/daimonkey.md):favicon/侧栏品牌/助手宠物/面板标题;炫彩版仅 favicon 等强调场景。
- 后续工作台会持续新增功能——新功能先在本专家登记需求再动工。

## 需求登记(2026-10-19)

- **云剪贴板**:工作台新增「剪贴板」页面,双用途——①两台设备间文本剪贴板同步(存入/一键复制,5 秒轮询增量拉新,滚动保留最近 100 条);②小云盘(文件上传/下载/删除,单文件 ≤100MB、每用户总量 1GB,registry.yaml `clipboard:` 块可调)。
- 用户隔离沿用 owner 模式(同 assistant_sessions);文件落 `data/clipboard/{owner}/`,库内存相对路径(仿 save_raw);上传走 raw body + X-Filename 头(避开 multipart/cgi)。

## 已实现(2026-10-17)

- 用户隔离全链路:知识库页顶部「我的知识库」配置卡(登录后显示)→ `GET/POST /api/v1/assistant/knowledge-repo` → `userctl repo` 同效;`sync_knowledge.py --user X` 按 `KNOWLEDGE_USER` 环境变量把仓库/输出/搜索索引全部切到 `knowledge-{user}` 命名空间;serve 按 Cookie 将 `/knowledge*` 路由到该用户独立构建(`dist/knowledge-{user}/`),未配置回落全局 InferTechNote;「⟳ 同步知识库」按钮自动带当前用户。
- 端到端验证:匿名 401、demo 用户命中独立页面、搜索走用户索引、子页路由正确。

## 运维注意

- 用户仓库同步依赖 GitHub SSH 可达(2026-10-17 实测出现过一次 clone 超时,重试即可);用户仓库需为公开仓库或本机 SSH key 有权限。
- 清理某用户:删 users 行 + `data/knowledge-{user}-repo`、`data/knowledge_search-{user}.json`、`dist/knowledge-{user}*`。

## 排障

| 症状 | 处置 |
|---|---|
| 用户配了仓库但知识库没变 | 当前渲染仍走全局快照(待实现 2);检查 `userctl list` 的配置 |
| 仓库地址被拒 | 仅接受 https://github.com/... 或 git@github.com:...,同步走 SSH |
