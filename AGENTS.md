# Agent 项目协作总约束

本文件适用于仓库中的所有 Codex 子 Agent。角色的详细边界见 `docs/agents/*.md`。

## 开工前必读

1. `docs/superpowers/specs/2026-07-26-agent-vue-fastapi-design.md`
2. `docs/contracts/openapi-v1.yaml`
3. `docs/contracts/api-semantics.md`
4. 自己角色对应的 `docs/agents/*.md`
5. `docs/governance/ownership-and-approvals.md`
6. `docs/quality/acceptance-gates.md`
7. `docs/security/minimum-production-baseline.md`
8. `docs/testing/apifox-cli.md`

若文件尚未生成或内容冲突，停止实现并向主决策者报告；不得自行猜测契约。
若设计规范仍标记为 Draft，所有角色只能审阅或制作获准的静态视觉稿，不得开始生产实现。

## 不可协商规则

- 生产实现只能修改角色拥有的目录；跨目录变更先交给主决策者协调。
- OpenAPI 是 HTTP 与 SSE 契约的唯一事实来源；前后端不得各自定义同义类型。
- Apifox CLI 用于黑盒 API 场景回归和报告生成，不得取代 OpenAPI、pytest 或最终真实联调。
- 不新增重复 API，不绕过分层直接访问数据库、Redis、向量库或模型。
- 不提交密钥、个人数据、日志、数据库文件、向量库产物或构建产物。
- 视觉审批前，前端只交付企业级静态稿和 Mock 状态；审批后必须完成真实联调。
- 任何重大功能必须包含成功、失败、超时或取消路径的自动化测试。
- 子 Agent 不得自行推送远程、合并主分支或更改冻结契约。
- 遇到契约、测试或安全门不满足时，报告阻塞，不得降低标准伪装完成。

## 完成交接格式

每次交付必须列出：

- 修改文件与目的；
- 契约、数据迁移和安全影响；
- 执行过的命令与结果；
- 未解决风险；
- 当前提交 SHA（若已提交）；
- 是否建议进入 QA。
