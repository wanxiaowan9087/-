# 文件所有权、审批与集成治理

## 1. 唯一所有权

| 范围 | 生产代码 owner | 测试 owner | 说明 |
|---|---|---|---|
| `frontend/src/**` | 前端工程师 | QA 可新增 `*.spec.*` | QA 不重写生产实现 |
| `backend/app/api/**` | 后端平台工程师 | QA | HTTP/SSE 适配 |
| `backend/app/schemas/**` | 后端平台工程师 | QA | 必须由 OpenAPI 生成或对齐 |
| `backend/app/application/**` | 后端平台工程师 | QA | 用例与事务边界 |
| `backend/app/domain/**` | 后端平台工程师 | QA | 共享领域状态，由主决策者审查 |
| `backend/app/repositories/**` | 后端平台工程师 | QA | 持久化接口 |
| `backend/app/adapters/sql/**` | 后端平台工程师 | QA | PostgreSQL |
| `backend/app/adapters/redis/**` | 后端平台工程师 | QA | Redis |
| `backend/app/adapters/llm/**` | AI/RAG 工程师 | QA | 模型与 Fake Model |
| `backend/app/adapters/vector/**` | AI/RAG 工程师 | QA | Chroma/VectorStore |
| `backend/app/agent/**` | AI/RAG 工程师 | QA | ReAct、工具、安全 |
| `backend/app/rag/**` | AI/RAG 工程师 | QA | 摄取、检索、重排、引用 |
| `backend/app/core/**` | 后端平台工程师 | QA | 配置、日志、异常、生命周期 |
| `backend/migrations/**` | 后端平台工程师 | QA | Alembic |
| `backend/tests/**` | 对应生产 owner 写首轮；QA 补充 | QA | QA 拥有最终验收 |
| `frontend/**` 内测试文件 | 前端工程师写首轮 | QA | 同上 |
| `evals/datasets/**` | AI/RAG 工程师提议 | QA 冻结 | 基线后不得为迎合实现改答案 |
| `evals/results/**` | QA | QA | 记录提交 SHA 与环境 |
| `tests/apifox/**` | QA | QA | 场景、套件导出文件与非敏感测试数据 |
| `docs/prompts/**` | AI/RAG 工程师提议 | 主决策者审批 | Prompt 变化必须带评测 |
| `docs/contracts/**` | 主决策者 | QA 审查 | 变更需要用户批准 |
| `docker-compose.yml`、容器文件 | 后端平台工程师 | QA | 前端工程师负责前端 Dockerfile |
| `.github/workflows/**` | QA 提议 | 主决策者审批 | CI 门 |
| 根 `README.md` | 主决策者整合 | QA 按步骤复验 | 各 owner 提供模块片段 |

同一文件若横跨两个 owner，由主决策者指定一次性 owner；其他角色只提交建议或补丁说明，禁止并发编辑。

## 2. 分支与集成顺序

```text
main
└─ codex/agent-vue-fastapi
   ├─ codex/frontend-static
   ├─ codex/backend-platform
   ├─ codex/ai-rag
   └─ codex/qa
```

1. 主决策者建立集成分支和独立 worktree。
2. 静态前端先交用户确认视觉基线。
3. 后端平台和 AI/RAG 只依赖冻结契约并行开发。
4. 主决策者按“平台 → AI/RAG → 前端动态联调”顺序集成，解决共享领域接口。
5. QA 必须在最终集成 SHA 上重新执行全量验收；子分支通过不能替代最终回归。
6. 最终集成 SHA 通过后才允许申请合并；禁止直接推送 `main`。

## 3. 审批记录

审批记录存入 `docs/approvals/`，文件名为 `YYYY-MM-DD-<type>.md`，至少包含：

```yaml
type: visual | api | architecture | destructive-data | git-push
artifact: 文件、预览地址或目标远程
commit_sha: 被审批的完整 SHA
approved_by: 用户
approved_at: 带时区时间
scope: 本次批准覆盖的内容
decision: approved | rejected
notes: 可选
```

- 视觉批准绑定静态稿提交 SHA；布局、品牌色、字体体系或核心交互层级发生实质变化时重新审批。
- API 批准绑定 OpenAPI 文件 SHA；新增/删除接口、字段语义或兼容性破坏时重新审批。
- Git 推送批准只覆盖明确的远程、分支和待推送提交范围；后续新提交需要重新报告并批准。
- 聊天中的用户明确同意可作为原始证据，主决策者负责落成记录；不得伪造审批。

## 4. 重大决策定义

下列任一项属于重大决策：更换数据库、向量库、模型供应商或 Agent 框架；新增外部服务；修改冻结 API；不可逆迁移；删除用户数据；改变视觉基线；降低质量或安全门。

重大决策流程：提出问题和证据 → 给出推荐方案与替代方案 → 说明迁移、测试和回滚 → 获得用户批准 → 创建或更新审批记录 → 实施。
