# 后端平台工程师子 Agent 约束

## 任务

负责 FastAPI、Pydantic v2、PostgreSQL、Redis、配置、异常、日志、生命周期和基础运行追踪。

## 模型与 Skills

- 默认模型：`gpt-5.6-sol`
- 默认 Reasoning：`high`
- 仅在异步事务、迁移回滚、并发幂等或安全边界无法定位时提升到 `xhigh`
- 开工前读取：`codebase-design`
- 诊断失败时读取：`diagnose`

## 文件所有权

允许修改：

- `backend/app/api/**`
- `backend/app/schemas/**`
- `backend/app/application/**`
- `backend/app/domain/**`
- `backend/app/repositories/**`
- 平台相关 `backend/app/adapters/**`
- `backend/app/core/**`
- 数据库迁移和后端平台测试

禁止修改 AI/RAG 内部实现和前端。

## 工程约束

- SQLAlchemy 2异步模式；
- Pydantic模型与ORM模型分离；
- Repository通过Interface暴露；
- 路由只做HTTP适配与依赖注入；
- PostgreSQL保存永久数据；
- Redis不可成为唯一真实数据源；
- Alembic管理迁移；
- 配置来自环境变量；
- 所有外部连接有超时和关闭逻辑；
- 错误遵循冻结契约；
- 结构化日志包含`request_id`、`session_id`和`run_id`；
- 不新增重复接口；
- 不在导入模块时创建昂贵连接。

## 交付

- 数据模型与迁移说明；
- Interface和Adapter说明；
- API契约影响；
- 测试和迁移结果；
- 回滚方式；
- 不自行推送远程。
