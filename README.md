# ZENMOP Agent 平台

面向 AI 应用开发实习展示的 Vue 3 + FastAPI Agent/RAG 工程。项目把旧版单体 Agent 拆分为可测试的前端、平台 API、RAG、Agent Runtime 和人工审核链路。

## 已实现

- Vue 3 + TypeScript：统一 API 客户端、POST-SSE 解析、幂等键、重放序号、开发代理与状态管理。
- FastAPI + 异步 SQLAlchemy：会话、消息、运行追踪、反馈、记忆、审核、SSE 恢复和 OpenAPI 契约。
- Agent/RAG：LangChain ReAct 适配器、混合检索（向量 + BM25 + RRF + rerank）、引用校验、提示注入防护、人工审核兜底。
- 记忆：短期会话历史和来源可追溯的长期偏好记忆；只保守提取明确偏好，避免猜测和隐私滥存。
- 质量：RAG 评测、API/SSE 契约漂移测试、QA 资产、前端单测、Ruff、类型检查和生产构建。

## 本地启动

所有依赖缓存建议放在 D 盘，例如 `D:\codex_store`。

1. 复制 [`.env.example`](.env.example) 为 `.env`，开发演示保持 `APP_AGENT_RUNTIME_ENABLED=false`。
2. 启动基础服务和后端：

   ```powershell
   docker compose up --build
   ```

3. 另开终端启动前端：

   ```powershell
   Set-Location frontend
   Copy-Item .env.example .env.local
   npm ci
   npm run dev
   ```

4. 浏览器访问 `http://localhost:5173`。开发鉴权令牌格式为 `subject:role`；前端 `.env.local` 的 `local-user:user` 仅适用于本地 demo。

## 启用真实模型

安装 AI 额外依赖并配置 DashScope 凭据后，将 `APP_AGENT_RUNTIME_ENABLED=true`：

```powershell
$env:PIP_CACHE_DIR='D:\codex_store\pip-cache'
pip install --require-hashes -r requirements.lock
$env:DASHSCOPE_API_KEY='your-key'
```

真实运行时使用 `qwen3-max` 与 `text-embedding-v4`，生产环境通过 PostgreSQL + pgvector 持久化向量数据，并以 HNSW 索引支持余弦相似度检索；服务启动时从 pgvector 分片重建内存 BM25 索引，因此重启后仍保持向量 + 词法的混合检索。开发环境可使用配置的本地 JSON 向量适配器，不能将其描述为生产数据源。

知识导入不新增面向普通用户的 HTTP 接口，而是使用显式的运维命令和版本化 JSONL 清单（示例见 [`docs/knowledge-manifest.example.jsonl`](docs/knowledge-manifest.example.jsonl)）：

```powershell
python -m backend.scripts.ingest_knowledge --manifest docs/knowledge-manifest.example.jsonl --dry-run
python -m backend.scripts.ingest_knowledge --manifest my-knowledge.jsonl
```

第二条命令需要 AI extra 与 `DASHSCOPE_API_KEY`，会对同一 `document_id` 的旧版本进行幂等替换；没有配置密钥时不会尝试伪造向量数据。

## 验证

```powershell
python -m pytest backend/tests tests/qa
python -m evals.run_rag_eval --assert-gates
Set-Location frontend
npm run lint; npm run typecheck; npm run test -- --run; npm run build
```

RAG 基线仍处于 `proposed_for_qa_review`，但已绑定到对应实现提交并记录 claim-to-source 引用评测结果。
