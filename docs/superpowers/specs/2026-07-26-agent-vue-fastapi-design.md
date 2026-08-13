# 智扫通 Agent：Vue 3 + FastAPI 工程化改造设计

> 状态：Approved。用户于 2026-07-26 明确要求“开始运行”；批准基线为 `65c08f32f5f61b74791b260a02fd62ce3fe91bd1`。前端视觉风格仍需在静态稿完成后单独审批。

## 1. 目标

将现有 Streamlit + LangChain ReAct 课程项目改造为达到 AI 应用开发实习作品集水准的前后端分离项目。

项目必须证明以下能力：

- Vue 3 企业级界面与流式交互；
- FastAPI、Pydantic v2、异步数据库和清晰分层；
- LangChain ReAct Agent、工具调用与运行追踪；
- 可溯源 RAG、混合检索、重排与固定评测集；
- 短期会话和长期记忆；
- 错误降级、人工审核和安全约束；
- Docker Compose 一键启动；
- 自动化测试与可重复验证。

项目不追求大规模微服务、Kubernetes、模型训练平台或复杂权限系统。

## 2. 用户审批门

以下事项必须由用户审批：

1. 前端第一版企业级静态稿的视觉风格；
2. 新增超出冻结契约的 API；
3. 更换核心数据库、向量库、模型供应商或 Agent 框架；
4. 删除、迁移或不可逆修改现有数据；
5. 任何远程 Git 推送。

静态稿通过审批后，前端必须与后端完成真实联调；不得停留在 Mock 数据演示。

每次推送前必须报告目标仓库、分支、提交内容、测试结果和推送原因。

## 3. 团队结构

根 Agent 担任主决策者、集成者和 Git 协调者。四个子 Agent 分别负责：

| 子 Agent | 职责 |
|---|---|
| 前端工程师 | Vue 3 静态稿、交互、状态管理、流式协议消费 |
| 后端平台工程师 | FastAPI、Pydantic、PostgreSQL、Redis、路由与基础设施 |
| AI/RAG 工程师 | ReAct、工具、记忆、摄取、检索、重排、引用和审核策略 |
| 测试工程师 | 契约、单元、集成、端到端、RAG 评测和 Docker 冒烟测试 |

子 Agent 使用独立 Git worktree 和独立分支。禁止跨 worktree 修改文件。测试工程师可以添加测试和缺陷报告，但不直接重写生产实现。

受并发上限约束，执行采用分波次调度：前三个实现角色先运行，任一完成后再启动测试工程师。模型按任务风险分级，不默认让所有角色使用最高档：

| 角色 | 默认模型 / 推理 | 升档条件 |
|---|---|---|
| 根 Agent（主决策） | `gpt-5.6-sol` / `xhigh` | 架构冲突、最终集成和重大回滚决策 |
| 前端工程师 | `gpt-5.6-terra` / `medium` | 复杂流式状态、视觉系统或无障碍反复失败 |
| 后端平台工程师 | `gpt-5.6-sol` / `high` | 异步事务、迁移回滚、幂等并发或安全问题 |
| AI/RAG 工程师 | `gpt-5.6-sol` / `xhigh` | 默认保持，纯重构和测试修复可降为 `high` |
| QA 工程师 | `gpt-5.6-terra` / `high` | 跨服务根因或最终集成缺陷时临时升为 `gpt-5.6-sol` / `high` |

模型升档必须在交接报告中写明触发问题、尝试过的低档方案和预期收益；模型强度不能替代测试门。

## 4. 总体架构

```text
Vue 3 + TypeScript
        |
        | REST + 流式事件
        v
FastAPI API 层
        |
        v
Application 用例编排
   |          |           |
   v          v           v
Agent       RAG         Conversation
模块         模块          模块
   |          |           |
   v          v           v
LLM        Chroma      PostgreSQL
Adapter    Adapter      Repository
                 \
                  Redis（缓存、限流、短期状态）
```

## 5. 目录边界

```text
frontend/
  src/api/            # HTTP 与流式协议
  src/components/     # 可复用 UI
  src/features/       # chat、trace、review
  src/stores/         # Pinia
  src/types/          # 前端契约类型
  src/views/          # 页面

backend/
  app/api/v1/         # 路由与 HTTP 依赖
  app/schemas/        # Pydantic 请求、响应、事件
  app/application/    # 用例编排
  app/domain/         # 领域模型与规则
  app/repositories/   # 持久化接口
  app/adapters/       # SQL、Redis、Chroma、LLM Adapter
  app/agent/          # ReAct、工具、状态、安全
  app/rag/            # 摄取、分片、检索、重排、引用
  app/core/           # 配置、日志、异常、生命周期
  tests/

evals/                # 固定问题集、期望来源、结果
docs/                 # 架构、契约、角色约束、Prompt
```

路由不得直接调用 SQL、Redis、Chroma 或模型。Application 层通过小而稳定的 Interface 使用模块，具体技术由 Adapter 实现。

## 6. 数据职责

### PostgreSQL

保存用户、会话、消息、Agent Run、工具调用、引用、长期记忆、用户反馈和人工审核记录。

### Redis

只用于缓存、限流、分布式锁、短期运行状态和可过期数据。Redis 不得成为永久聊天记录的唯一来源。

### Chroma

第一阶段继续使用现有 Chroma，保存文档分片向量。通过 VectorStore Interface 隔离实现，以便未来替换 Qdrant 等向量库。

## 7. 会话与长期记忆

- 每次请求生成 `request_id`，每次 Agent 执行生成 `run_id`；
- 原始消息永久保存到 PostgreSQL；
- 短期上下文按会话窗口和摘要组合；
- 长期记忆分为用户事实、偏好和任务摘要；
- 每条长期记忆必须包含来源消息、置信度、创建时间和更新时间；
- 用户可以纠正、停用或删除长期记忆；
- 不把所有对话无差别向量化；
- 记忆提取失败不得阻塞主回答。

## 8. RAG 数据流

```text
加载
→ 清洗与文档版本
→ 按文档类型选择分片策略
→ Embedding
→ 向量索引
→ 向量检索 + BM25
→ RRF 融合
→ Rerank
→ Top-K 上下文
→ 回答与引用
```

分片规则：

- FAQ 按问答对；
- Markdown 按标题层级；
- TXT 使用递归字符分片；
- PDF 保留页码；
- 表格保持行列语义；
- 每个分片保留 `document_id`、`version`、`chunk_id`、来源和位置。

回答引用必须可追溯到具体分片。知识库无依据时必须明确说明，不得拼凑答案。

## 9. 错误与人工审核

错误分为参数、认证、资源、状态冲突、限流、模型、工具、检索、存储和内部错误。

模型或工具异常需要：

- 超时；
- 有上限的重试；
- 指数退避；
- 可用时降级；
- 结构化日志；
- 面向用户的安全错误信息。

“低置信度”“高风险内容”和“关键工具”采用 `docs/quality/acceptance-gates.md` 的可验收定义。命中规则时 Run 进入 `needs_review`，其状态转换、并发控制和发布语义以 `docs/contracts/api-semantics.md` 为准。人工可以批准、驳回或编辑后发布，所有操作保留审计记录。

## 10. 前端设计流程

第一阶段生成企业级静态页面，包含会话侧栏、品牌头部、消息区、输入区、工具状态、引用卡片、运行追踪入口、人工审核状态以及空/加载/错误状态。

用户确认风格前不接真实接口。确认后依次实现：

1. 创建和恢复会话；
2. 流式文本与事件；
3. 停止生成和重试；
4. 工具执行状态；
5. 引用资料展示；
6. 运行追踪；
7. 人工审核；
8. 响应式与无障碍。

## 11. 测试与交付门

- Pydantic 校验与错误格式测试；
- API 契约测试；
- Apifox CLI 黑盒场景测试与 JSON/JUnit 报告；
- Fake Model 流式事件测试；
- Agent 工具成功、失败、超时和重试测试；
- Repository 集成测试；
- RAG 引用、拒答和重排测试；
- 20 至 30 条固定 RAG 评测集；
- Vue 核心模块测试；
- Playwright 端到端测试；
- Docker Compose 健康检查；
- 全量回归测试。

CI 默认使用 Fake Model 和固定 Embedding，避免付费模型导致测试不稳定。

完成标准：

- `docker compose up --build` 可启动项目；
- 前端和后端完成真实联调；
- `docs/quality/acceptance-gates.md` 第 4 节定义的全部阻断测试在最终集成 SHA 上通过且无跳过；
- README 能让新用户独立运行；
- 演示能展示流式对话、工具调用、引用、记忆和错误兜底；
- 不含密钥、日志、向量库产物和真实隐私数据。

## 12. Git 工作流

```text
main
└─ codex/agent-vue-fastapi
   ├─ codex/frontend-static
   ├─ codex/backend-platform
   ├─ codex/ai-rag
   └─ codex/qa
```

- 禁止直接推送 `main`；
- 禁止 force push、hard reset 和清理未确认文件；
- 每个提交只包含一个可解释变化；
- 合并前由测试工程师验证；
- 远程推送前必须向用户报告并获得确认。

## 13. 权威附属规范

以下文件与本设计共同构成实现前置条件：

- `docs/contracts/openapi-v1.yaml`：HTTP 与 SSE 的唯一机器可读契约；
- `docs/contracts/api-semantics.md`：幂等、取消、断线与审核状态机；
- `docs/governance/ownership-and-approvals.md`：唯一 owner、分支集成与审批证据；
- `docs/quality/acceptance-gates.md`：唯一验收入口、测试矩阵、RAG 阈值和术语定义；
- `docs/security/minimum-production-baseline.md`：单租户身份、隐私、恢复、观测和供应链底线；
- `docs/testing/apifox-cli.md`：Apifox 场景测试、报告、同步与密钥边界；
- `docs/agents/*.md`：四个子 Agent 各自的文件边界与交付格式。

若文字说明与 OpenAPI 的请求、响应或事件 schema 冲突，以 OpenAPI 为准；若实现便利与质量、安全或审批门冲突，以门禁为准并报告主决策者。任何附属规范变更都必须由其 owner 审查，不能由实现 Agent 为通过测试而自行放宽。
