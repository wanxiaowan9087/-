# API v1 状态机、幂等与流恢复语义

本文件解释 [`openapi-v1.yaml`](./openapi-v1.yaml) 中不容易仅靠字段类型表达的行为。机器结构以 OpenAPI 为唯一来源；本文不得单独新增字段、接口或状态码。

## 1. 身份与对象授权

项目是单租户演示系统，不实现组织、团队或复杂 RBAC。认证 Adapter 提供 `subject_id` 和 `role`：

- `user`：只能访问自己的会话、消息、Run、反馈和长期记忆；
- `reviewer`：具备 user 的普通能力，并可访问审核队列、作出审核决定；只能额外读取与其可见审核任务关联的 Run；
- `/health/live` 与 `/health/ready` 可匿名，但只返回脱敏状态。

服务端必须检查对象归属。对“资源不存在”和“资源属于其他用户”可统一返回 `404 NOT_FOUND`，避免枚举资源；角色明确不足返回 `403 FORBIDDEN`。

## 2. 通用响应与状态码

流开始前的成功和失败均使用 OpenAPI 定义的 JSON envelope，并使用真实 HTTP 状态码。Pydantic 结构错误是 `422 VALIDATION_ERROR`；可解析但业务语义错误是 `400 BAD_REQUEST`；版本、幂等、唯一性和状态竞争是 `409`。

SSE 响应头已经发送后不得伪造新的 HTTP 状态。此后的业务失败使用唯一终止 `error` 事件并关闭连接，服务端日志保存内部原因，事件只提供安全信息。

## 3. Idempotency-Key

### 3.1 作用域与保存

所有会创建、修改、删除或触发执行的接口都要求 `Idempotency-Key`。服务端至少保存 24 小时，绑定：

```text
authenticated_subject + operation_id + normalized_path + canonical_request
```

`canonical_request` 包含请求体及影响语义的查询/条件头，但不包含链路追踪头。服务端保存请求摘要、执行状态、原始 HTTP 状态码、响应体或流对应的 `run_id`；不得在日志中保存 key 原文。

### 3.2 决策表

| 情况 | 行为 |
|---|---|
| 新 key | 原子占位后执行 |
| 同 key、同规范请求、原执行完成 | 重放相同 HTTP 状态码和结果，`Idempotency-Replayed: true` |
| 同 key、同规范请求、原执行仍进行 | `409 IDEMPOTENCY_IN_PROGRESS` |
| 同 key、请求不同 | `409 IDEMPOTENCY_KEY_REUSED` |
| key 记录超过保存期 | 作为新 key；客户端不得依赖过期重放 |

幂等记录与核心业务写入必须具有不会产生半完成副本的事务策略。Redis 可以加速检查，但 PostgreSQL 唯一约束或等价永久机制才是最终仲裁者。

## 4. 会话与消息

会话列表按 `last_message_at DESC, id DESC` 稳定排序。消息按 `created_at ASC, id ASC` 排序。cursor 是不透明快照位置；非法、篡改或与筛选条件不匹配的 cursor 返回 `400 BAD_REQUEST`。

新聊天请求必须在同一事务意图中建立：

1. 一条 `user` 消息；
2. 一条助手消息占位；
3. 一个 `Run`；
4. 幂等记录与首个 `meta` 事件所需标识。

若进程在提交后、发送 `meta` 前中断，客户端以相同 key 重连，仍得到相同的 `user_message_id`、`assistant_message_id` 和 `run_id`。

## 5. Run 状态机

```text
queued -> running -> completed
                  -> needs_review -> completed
                                  -> rejected
                  -> cancelled
                  -> failed
```

- `completed`、`cancelled`、`failed`、`rejected` 是终止状态；
- `needs_review` 只等待 reviewer 决定，不继续调用模型或工具；
- 终止状态不可回到 running；
- 所有转换使用数据库条件更新或版本字段，重复/并发转换只有一个成功。

`assistant` 消息的状态跟随 Run，但审核候选在批准前不进入 user 可见的 `content`。批准或编辑后发布会原子写入消息内容、审核决定和审计记录；驳回保留审核审计但不发布候选。

## 6. 新消息与重试

`POST /chat/stream` 的 `mode` 是判别字段：

- `new`：为 `content` 创建新 user 消息与 Run；
- `retry`：校验 `original_user_message_id` 属于该用户和 session，复用原 user 消息创建一个新 Run 和新的 assistant 消息占位，不复制 user 消息。

同一 Idempotency-Key 的 retry 永远映射到同一 retry Run。不同 key 代表用户明确发起的新尝试；若同一原消息已有非终止 retry Run，则返回 `409 CONFLICT`，终止后允许再次重试。每个 retry Run 在 trace 中记录原 user 消息和 attempt 序号。

## 7. 服务端取消

断开 SSE、刷新页面或 `AbortController.abort()` 只停止客户端读取，不能证明用户希望停止服务端执行。

`POST /runs/{run_id}/cancel` 先持久化取消意图，再通知执行器：

- queued/running：返回 `202` 和 `cancellation_requested`；
- 已终止：返回 `200` 和 `already_terminal`；
- 与恰好完成并发时，数据库条件更新决定唯一结果，调用者读取返回状态或 trace；
- 模型/工具不支持硬取消时，执行器必须丢弃其迟到结果，不得在 cancelled 后发布消息。

可观察到的取消流为：

```text
meta -> ... -> status(cancelling) -> [tool_end(cancelled)] -> done(cancelled)
```

`done(cancelled)` 后不得再出现事件。

## 8. SSE 帧、顺序与终止

每帧采用：

```text
id: <sequence>
event: <event_type>
data: <符合 SseEvent schema 的单行 JSON>
```

同一 Run 的 `sequence` 从 1 开始严格递增、不重复。`meta` 必须是序列中的第一类事件；重放时客户端可能从中间 sequence 开始，因此本次 TCP 连接的第一帧不一定是 meta。

每个 Run 必须恰好出现一个终止事件：`done` 或 `error`。终止事件后不得出现其他事件。`tool_start` 的正常匹配是同 `tool_call_id` 的 `tool_end`；若进程级故障导致无法补齐，Run 以 `error` 终止，并由 trace 标记未完成 step。

### 8.1 正常序列

```text
meta
-> status(accepted)
-> [status | tool_start -> tool_end | citation]*
-> status(generating)
-> delta+
-> status(completed)
-> done(completed)
```

引用可以先于或后于 delta，但必须在 `done` 前完成，且最终消息中的引用集合与已发送 citation 事件一致。

### 8.2 审核序列

可能触发审核的候选内容必须先在服务端缓冲并完成策略检查，不得先向普通 user 流出随后又标记“需审核”。序列为：

```text
meta
-> status(accepted)
-> [status | tool_start -> tool_end | citation]*
-> status(checking_policy)
-> status(awaiting_review)
-> review_required(draft_withheld=true)
-> done(needs_review)
```

此序列不发送候选正文 delta。审核完成后客户端通过消息历史获得已批准内容；审核决定不会恢复已经终止的旧 SSE。

### 8.3 错误序列

```text
meta -> ... -> error
```

`error` 是终止事件，之后不再发送 `done`。可恢复性由 `retryable` 表达；它不授权客户端改用新 key 盲目重复产生 Run。连接级未知中断应先执行流恢复。

### 8.4 取消序列

见第 7 节。取消以 `done(outcome=cancelled)` 终止，不使用 `error` 表达用户主动取消。

## 9. SSE 断线恢复

服务端必须将事件以 `(run_id, sequence)` 唯一保存，并在 Run 终止后至少保留 24 小时。客户端在完整处理每帧后保存其 `id`：

1. 使用原始 Idempotency-Key；
2. 使用完全相同的 POST body；
3. 设置 `Last-Event-ID` 为最后完整处理的 sequence；
4. 服务端验证幂等绑定，重放更大 sequence，再跟随实时事件。

服务端可以重放相同业务事件，但不得为重放创建新的事件 ID。客户端按 `(run_id, sequence)` 去重。若请求的恢复点早于保留窗口，服务端在流建立前返回 `410 STREAM_REPLAY_EXPIRED`；客户端读取 session messages 与 `/runs/{run_id}/trace` 恢复最终状态。

## 10. 长期记忆状态

```text
active --correct--> active（version + 1，保留来源与纠正链）
active --deactivate--> inactive（version + 1）
active|inactive --delete--> deleted（普通列表不可见）
```

纠正和停用使用 `expected_version`，删除使用 `If-Match`；版本不一致返回 `409 CONFLICT`。纠正不得改写原始 source message。删除在线数据后立即停止检索，审计记录保留；备份副本按备份周期过期。

## 11. 人工审核状态

```text
pending -> approved
        -> rejected
        -> edited_and_published
```

决定只允许从 pending 发生一次。`expected_version` 防止两个 reviewer 覆盖；后来者得到 `409 CONFLICT`。approve 发布原候选，edit_and_publish 发布 reviewer 提交的正文，reject 不发布。每次成功或冲突尝试都记录 reviewer subject、request_id、时间、旧/新状态和内容摘要；审计不得保存认证令牌。

## 12. 契约测试最低断言

- 每个 operation 的角色、对象归属、全部显式状态码与 error envelope；
- 所有写接口缺少/复用/冲突 Idempotency-Key 的行为；
- new/retry 不产生重复 user 消息，并发 retry 只有一个运行态；
- SSE 九类判别 schema、sequence、四种序列和唯一终止；
- 断线重放、事件去重、过期 410，以及断线不等于取消；
- cancelled 后迟到的模型或工具结果不可发布；
- 记忆版本冲突、删除后不可检索；
- 审核重复决定与并发决定只有一个成功，未批准候选不泄露给 user。
