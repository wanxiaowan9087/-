# API v1 契约导航

`openapi-v1.yaml` 是 `/api/v1` HTTP、SSE、Pydantic 和前端类型的唯一机器可读事实来源；本文件只提供阅读入口，不复制字段定义。

配套文件：

- `openapi-v1.yaml`：请求、响应、状态码、错误码、角色、幂等头和 SSE 判别联合；
- `api-semantics.md`：Run、取消、重试、流恢复、记忆与人工审核状态机；
- `../quality/acceptance-gates.md`：契约测试、真实联调与最终集成 SHA 验收门。

## 拟冻结能力

以下能力在用户批准本设计及其绑定提交 SHA 后冻结；当前仍是 Draft，不代表已经获得 API 审批。

| 能力 | Method 与 Path |
|---|---|
| 存活 / 就绪 | `GET /health/live`、`GET /health/ready` |
| 会话 | `GET/POST /sessions`、`GET /sessions/{session_id}`、`GET /sessions/{session_id}/messages` |
| 对话 | `POST /chat/stream`；请求体用 `mode=new|retry` 区分新消息和重试 |
| 取消 / 追踪 | `POST /runs/{run_id}/cancel`、`GET /runs/{run_id}/trace` |
| 反馈 | `POST /chat/{message_id}/feedback` |
| 长期记忆 | `GET /memories`、`PATCH/DELETE /memories/{memory_id}` |
| 人工审核 | `GET /reviews`、`POST /reviews/{review_id}/decision` |

这组接口一次性覆盖当前批准设计所需的会话恢复、流式对话、停止、重试、可溯源运行、记忆纠正/停用/删除和审核流程。实现 Agent 不得再为同一语义创建别名接口。

## 变更规则

新增或不兼容修改前，必须提交用例、OpenAPI diff、权限、幂等语义、错误模式、迁移影响和测试方案，并获得用户明确批准。只修改本导航文字不能改变契约。
