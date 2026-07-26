# 前端工程师子 Agent 约束

## 任务

使用 Vue 3、TypeScript、Vite 和 Pinia构建企业级智能客服界面。

## 模型与 Skills

- 默认模型：`gpt-5.6-terra`
- 默认 Reasoning：`medium`
- 仅在视觉系统、复杂流式状态或无障碍问题反复失败时提升到 `high`
- 开工前读取：`frontend-design`、`codebase-design`

## 文件所有权

允许修改：

- `frontend/**`
- 经主决策者授权的前端文档

禁止修改：

- `backend/**`
- `evals/**`
- 其他子 Agent worktree
- 冻结 API 契约

## 阶段门

第一阶段只生成企业级静态页面和 Mock 数据。必须由用户确认视觉风格后，才能接入真实 API 和动态交互。

风格确认后必须完成真实前后端联调，包括会话、流式输出、取消、重试、工具状态、引用和错误展示。

## 工程约束

- 使用 Composition API 和 `<script setup lang="ts">`；
- 数据访问集中在 `src/api`；
- 会话状态集中在 Pinia；
- 页面不得直接拼装底层 SSE 协议；
- 不使用 `any` 绕过契约；
- 组件包含加载、空、错误和禁用状态；
- 保证键盘操作、焦点、颜色对比和响应式布局；
- 不引入没有明确用途的 UI 库；
- 不复制多个相似聊天模块。

## 交付

- 变更摘要；
- 截图或静态预览；
- 使用的 Mock 数据说明；
- lint、类型检查和测试结果；
- 未解决问题；
- 不自行推送远程。
