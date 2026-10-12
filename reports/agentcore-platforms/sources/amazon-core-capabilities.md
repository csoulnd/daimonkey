---
title: AgentCore 七大核心能力（亚马逊云科技官方号）
source: 亚马逊云科技（官方号文章）
url: https://zhuanlan.zhihu.com/p/1929547160770295073
accessed: 2026-10-12
note: 报告来源素材——仅收录官方定义口径，已去除营销表述与发布/计费信息
---

# Amazon Bedrock AgentCore · 七大核心能力

Amazon Bedrock AgentCore 提供一整套部署并高效运行 AI Agents 的能力，支持使用托管于 Amazon Bedrock 或其他服务的**任意框架和模型**。

## 七大核心能力

| 核心能力 | 官方定义 |
|---|---|
| **Runtime** | 安全、弹性的 Agent 运行环境；支持长时异步工作负载与完整会话隔离 |
| **Memory** | 提供长期和短期记忆，支撑上下文感知的 Agent |
| **Identity** | Agent 身份验证与授权，安全访问工具与资源；可集成 Cognito、Entra ID、Okta 等身份服务 |
| **Gateway** | 工具的发现与调用通道；将 API、Lambda 函数及现有服务转换为 Agent 可用的工具 |
| **Code Interpreter** | 在安全沙箱中编写并执行代码；执行环境的实例类型与会话参数可自定义 |
| **Browser Tool** | 云上浏览器工具，支持表单填写、网页导航等网站交互任务 |
| **Observability** | 基于 Amazon CloudWatch 的运行追踪与指标；内置仪表板与 telemetry，可对接现有可观测性系统 |

## 组合方式

- 七项服务**既可单独使用，也可协同工作**；
- 兼容开源或自定义的 Agent 框架，通过 **AgentCore SDK** 集成到现有代码；
- 借助 Runtime，Agent 与工具可通过 Gateway 安全连接云市场（Marketplace）中基于 API 的 Agent 和工具。
