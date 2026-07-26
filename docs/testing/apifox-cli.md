# Apifox CLI 测试规范

## 1. 定位

Apifox CLI 负责从客户端视角验证真实 FastAPI 服务，包括请求编排、变量提取、数据驱动断言和测试报告。它是 `quality_gate.py apifox` 的实现工具，并由唯一总入口 `quality_gate.py all` 调用。

测试职责保持分层：

| 工具 | 权威范围 |
|---|---|
| OpenAPI | Method、Path、schema、状态码、错误码和 SSE 事件结构 |
| pytest | Pydantic、领域规则、事务、故障注入、SSE 逐事件状态机和内部 Adapter |
| Apifox CLI | 运行中服务的黑盒 REST/场景链路、权限、幂等和报告 |
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
  scenarios/          # 可离线运行的场景或套件导出文件
  data/               # 不含隐私的 JSON/CSV 数据
  README.md           # 导出版本、场景 ID、环境变量名和更新方式
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

## 5. CI 运行模式

- 合并门优先运行仓库内已审查的离线导出场景，避免 Apifox 云端资源变化导致同一 SHA 得到不同结果。
- 在线项目场景可用于手工或定时补充回归，但不能成为最终集成 SHA 的唯一证据。
- 实际命令由当前安装版本的 `apifox run --help` 和 Apifox 客户端 CI/CD 面板生成；不得在规范中锁死未经当前版本验证的参数组合。
- CI 必须请求 `cli`、`json`、`junit` 报告；可选 `html`。输出统一进入 `artifacts/apifox/`，失败退出码阻断 `quality-api-scenarios`。
- `quality_gate.py` 记录 Apifox CLI 版本、场景文件摘要、运行环境、目标提交 SHA、用例数量和报告路径。

## 6. 变量与密钥

- `APIFOX_ACCESS_TOKEN`、Bearer Token、数据库密码和私有变量文件只通过 CI secret 或本机私有文件注入。
- 禁止把 token 放在命令文本、仓库、Prompt、截图或测试报告中。
- 优先使用临时/环境变量覆盖测试身份，不依赖个人电脑中的 Apifox 本地值。
- 变量文件必须提供无密钥的 `.example`，私有版本使用 `*.private.*` 并由 Git 忽略。
- 报告上传云端默认关闭；若需要 `--upload-report`，必须确认报告已脱敏且获得主决策者批准。

## 7. 安装约束

Apifox CLI 依赖 Node.js。当前机器未安装 CLI，且本项目禁止未经确认向 C 盘全局安装。实施阶段优先把 CLI 作为锁定版本的开发/CI 依赖放入项目工具链或 D 盘明确目录；安装位置、版本和空间占用先报告用户，再执行安装。
