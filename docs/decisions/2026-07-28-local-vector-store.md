# 本地持久化 VectorStore 决策

日期：2026-07-28

## 决策

移除 `chromadb` 依赖和 Chroma `PersistentClient` 运行路径，改用
`JsonVectorStore`。该适配器仍满足 `VectorStorePort`：文档版本替换、余弦
检索、按 chunk 定位和重启后的 BM25 重建。

## 原因

依赖审计报告 `chromadb==1.5.9` 存在 `PYSEC-2026-311`，且没有可用的上游
修复版本。即使本项目此前使用嵌入式客户端而非公开 Chroma 服务，质量门禁
仍不能可靠通过。

## 运行边界

该实现面向单个后端进程、实习演示和中小型知识库。写入采用临时文件替换，
防止单次写入留下半个 JSON 文件；它不是多节点并发向量数据库。未来如需水平
扩展，应在既有 `VectorStorePort` seam 上接入受支持的 PostgreSQL/pgvector
或 Qdrant Adapter。

## 数据迁移

旧 Docker 卷 `agent-chroma` 未被删除或修改。其历史向量数据不会自动转换；
需要使用现有知识库 manifest 重新运行 `backend.scripts.ingest_knowledge`，
写入新的 `agent-vector-data` 卷。
