# 全用户使用总结实施计划

**设计依据：** `docs/superpowers/specs/2026-08-20-user-usage-summary-design.md`（`3c67da0`）

## 阶段 1：冻结契约与审批证据

1. 在 `docs/contracts/openapi-v1.yaml` 新增 `GET /me/usage-summary`、响应 Envelope、快照和空设备数据 Schema。
2. 在 `backend/app/schemas/resources.py` 建立等价 Pydantic 模型；在 `backend/tests/test_openapi_drift.py` 和契约模型测试中验证一致性。
3. 将本轮聊天中的用户批准写入 `docs/approvals/`，覆盖新增只读接口、`user_id` 隔离、自动更新和保留周期。该审批不涵盖远程推送。

## 阶段 2：领域与 PostgreSQL 持久化

1. 在 `backend/app/domain/records.py` 新增事件、汇总任务、总结快照和清理结果的 typed records；在 `backend/app/repositories/ports.py` 声明必须以 `owner_id`/`user_id` 为参数的读写方法。
2. 在 `backend/app/adapters/sql/models.py` 和 `repository.py` 实现三类表、所有权 SQL 过滤、任务唯一约束、并发领取锁、原子 active 快照切换和批量清理查询。
3. 新建 Alembic 迁移，创建 `user_usage_events`、`summary_update_jobs`、`user_summary_snapshots`、外键、状态约束和索引；空库升级、已有库升级与重复运行均可通过。
4. 为仓储增加 PostgreSQL 级测试：用户隔离、重复投递幂等、并发领取、重试状态、快照切换及到期删除边界。

## 阶段 3：汇总、记忆和任务 worker

1. 新建应用服务：`MemoryExtractionService` 复用已有记忆提取能力，`UserUsageAggregator` 汇总站内真实消息与事件，`UserSummaryService` 生成稳定 JSON 和中文展示摘要。
2. 在 `backend/app/application/streaming.py` 的成功终态写入同一事务中创建汇总任务；失败、取消、审核中与拒绝回答不生成成功汇总任务。
3. 在 `backend/app/main.py` 生命周期启动可恢复 worker。worker 从数据库领取待处理任务，使用有限重试和退避，记录脱敏错误；多实例使用 PostgreSQL 锁避免重复处理。
4. 将定期清理作为同一 worker 的每日低峰任务，以 PostgreSQL 锁串行运行，严格执行 14/60/90/30/7 天以及 12 个快照的保留规则。
5. 使用可控时钟和 Fake Model 测试：聊天不被汇总阻塞、服务重启可恢复、失败重试、无数据结果、清理保护和日志脱敏。

## 阶段 4：Agent 与 MCP

1. 在 `backend/app/agent/customer_tools.py` 或独立模块注册 `get_user_usage_summary` MCP 工具；身份只能从运行上下文读取，禁止工具参数指定用户。
2. 让 Agent 对“总结我的使用情况”等意图调用快照工具，替换仅按当前会话摘要进行的临时回答；保留原有长期记忆工具用于个人资料和偏好查询。
3. 记录推荐事件时只写入 `product_id`、`model_code` 和来源消息；展示仍从产品目录映射。
4. 添加 Agent 测试，覆盖当前用户、空快照、不同用户隔离、无设备数据和产品映射失效。

## 阶段 5：HTTP、前端行为与真实联调

1. 在 `backend/app/application/service.py` 与 `api/v1/routes.py` 增加只读 `GET /me/usage-summary`，从 `Principal.subject_id` 获取用户范围。
2. 在产品详情和 3D 操作的真实前端交互处调用受认证的事件写入路径；不向前端暴露任意用户 ID。
3. 增加“我的使用总结”读取状态，涵盖加载、无数据、更新中和错误；产品推荐通过已有目录显示图片、价格与跳转。
4. Playwright/前后端集成验证：两用户隔离、对话结束后更新、空设备状态和推荐展示。

## 阶段 6：最终验收与交付

1. 执行数据库迁移测试、pytest、OpenAPI 漂移检查和 API 场景测试。
2. 在 Docker Compose 中执行真实 PostgreSQL 前后端联调，确认 worker、清理任务、历史会话与总结读取正常。
3. 执行 `python scripts/quality_gate.py all --integration-sha <完整SHA>`；若现有质量脚本缺少新场景，先补充再运行。
4. 整理修改文件、迁移影响、验证结果、已知风险和提交 SHA；通过后向用户请求针对准确 SHA 的 GitHub 推送批准。

## 实施顺序与提交策略

- 提交一：契约、领域模型、迁移和仓储测试。
- 提交二：worker、汇总服务、MCP 和后端测试。
- 提交三：HTTP、前端事件/总结界面和联调测试。
- 提交四：验收脚本或场景补全与质量报告（若产生可提交的非运行产物）。

每一提交仅包含本功能的关联文件；不纳入工作区中既有的 README、图片、启动脚本或 `artifacts/` 改动。远程推送须等待用户针对最终提交范围的单独确认。
