# Apifox 场景验收与离线替代规范

## 1. 定位

Apifox 在线场景用于人工验证真实 FastAPI 服务，包括请求编排、变量提取和数据驱动断言。2026-07-29 已验证 Apifox CLI 2.2.8 与桌面端，但桌面端“导出数据运行”仍不可用，且普通项目导出不能由当前 CLI 运行。经用户明确批准，`quality_gate.py all` 的离线阻断场景改为仓库内的 `backend/tests/test_api_scenarios.py`；Apifox 在线 27/27 报告仅保留为补充验收证据。

测试职责保持分层：

| 工具 | 权威范围 |
|---|---|
| OpenAPI | Method、Path、schema、状态码、错误码和 SSE 事件结构 |
| pytest | Pydantic、领域规则、事务、故障注入、SSE 逐事件状态机和内部 Adapter |
| Apifox 在线场景 | 人工补充验证运行中的 REST/场景链路、权限与幂等 |
| 仓库 API 场景测试 | 可重复的 HTTP 场景链路、权限、幂等与 JUnit 报告 |
| Playwright | 浏览器到 FastAPI 的真实前后端联调 |
| RAG evals | 检索、排序、引用、拒答与注入防御指标 |

Apifox 用例通过不能替代其他层；同一个业务风险可在不同层有不同粒度的断言。

## 2. 契约同步

- 仓库中的 `docs/contracts/openapi-v1.yaml` 是唯一事实来源。
- Apifox 项目从该文件导入或同步；禁止只在 Apifox UI 修改接口后不回写仓库设计。
- 同步后必须比较 Method、Path、operationId、请求/响应 schema 和状态码；有漂移时阻断 CI。
- 修改 OpenAPI 仍遵守 API 用户审批门，Apifox 同步不构成审批。

## 3. 用例资产

```text
tests/apifox/
  scenarios/          # 可审阅的场景意图清单（非可执行 vendor 导出）
  data/               # 不含隐私的 JSON/CSV 数据
  README.md           # 场景 ID、替代原因和更新方式
  *.private.*         # 本地私有变量，Git 忽略
```

QA 是该目录 owner。场景文件变更必须说明对应 OpenAPI 版本、被测用例、预期断言和重新导出原因。禁止为了让失败实现通过而删除断言。

## 4. 必测场景

至少包含：

1. 创建会话 → 发送新消息 → 查询消息 → 查询 Run trace；
2. 使用相同 `Idempotency-Key` 重放，以及同 key 不同请求的 `409`；
3. retry 复用原 user message 且产生新 Run；
4. 服务端取消与重复取消；
5. 分页、非法 cursor、未知资源与对象级越权；
6. 反馈创建与重复提交；
7. 记忆列表、纠正、停用、删除和版本冲突；
8. reviewer 审核批准、驳回、编辑发布、重复决定和非 reviewer 的 `403`；
9. 参数校验、认证失败、限流和依赖不可用的安全错误 envelope；
10. 所有响应的 `request_id`、HTTP 状态码和关键 schema 断言。

`/chat/stream` 可以由 Apifox 场景验证能建立连接和最终业务结果，但 `sequence`、九类事件 payload、断线重放与唯一终止仍必须由 pytest 的流式集成测试负责。

当前离线替代场景对 APIFOX-009 覆盖 `422`、`401` 和依赖不可用 `503`。运行时限流器尚未实现，因此没有诚实、可重复的 `429` 触发条件；该缺口记录在 `docs/quality/apifox-progress.md`，不得将现有 9/9 场景结果表述为已验证限流行为。

## 5. CI 运行模式

- 合并门运行 `python scripts/quality_gate.py api-scenarios --integration-sha <HEAD_SHA>`，由 pytest 在 FastAPI ASGI 边界执行 APIFOX-001 至 APIFOX-009。
- CI 输出 JUnit 至 `artifacts/quality/api-scenarios/junit.xml`，失败退出码阻断 `quality-api-scenarios`；总验收报告记录目标提交 SHA、用例数与运行结果。
- 在线项目场景可用于手工或定时补充回归，但不能成为最终集成 SHA 的唯一证据，也不要求为 CI 提供 access token。

## 6. 变量与密钥

- `APIFOX_ACCESS_TOKEN`、Bearer Token、数据库密码和私有变量文件只通过 CI secret 或本机私有文件注入。
- 禁止把 token 放在命令文本、仓库、Prompt、截图或测试报告中。
- 优先使用临时/环境变量覆盖测试身份，不依赖个人电脑中的 Apifox 本地值。
- 变量文件必须提供无密钥的 `.example`，私有版本使用 `*.private.*` 并由 Git 忽略。
- 报告上传云端默认关闭；若需要 `--upload-report`，必须确认报告已脱敏且获得主决策者批准。

## 7. 安装约束

Apifox CLI 依赖 Node.js。当前机器已有 `D:\apifox\Apifox.exe` 桌面端，但它不是 PATH 中可调用的 CLI。当前项目禁止未经确认向 C 盘全局安装。桌面端继续用于人工编排与在线验收；当前离线质量门禁不依赖 CLI 或 vendor 导出文件。
