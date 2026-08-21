# 用户使用总结 API 与架构审批记录

```yaml
type: api
artifact: docs/contracts/openapi-v1.yaml#/paths/~1me~1usage-summary
commit_sha: e28481c
approved_by: 用户
approved_at: 2026-08-20T23:20:00+08:00
scope: 新增 GET /api/v1/me/usage-summary 与幂等 POST /api/v1/me/usage-events（仅详情页和 3D 查看）；身份从 Bearer 令牌确定；总结与行为按 user_id 隔离；每轮助手完成后异步更新；推荐型号保留产品目录映射；14/60/90/30/7 天数据保留周期与最近 12 个总结快照
decision: approved
notes: 用户明确表示“开始”实施已确认设计；不包含 GitHub 远程推送授权。
```
