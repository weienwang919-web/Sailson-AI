# TODOS

技术债务和未来工作跟踪。由 /plan-ceo-review 于 2026-03-27 生成。

---

## P2 — 前后端分离

**What:** 用 Vue.js 或 React 替换 Jinja2 服务端渲染，实现 SPA 体验。
**Why:** 当前 13 个 HTML 模板各自独立，代码重复多、交互体验差、无法复用组件。
**Pros:** 更好的 UX（无刷新导航）、可复用组件库、移动端适配、前后端独立开发。
**Cons:** XL 工作量、需要引入前端构建工具链、团队需要前端框架知识。
**Context:** 等 Blueprint 拆分完成后进行。Blueprint 拆分会自然产生清晰的 REST API 边界，是前后端分离的前提。
**Effort:** XL (human: ~3周 / CC: ~2小时)
**Priority:** P2
**Depends on:** Blueprint 拆分（提案 1）

---

## P2 — 数据库迁移版本控制

**What:** 引入 Alembic 或简单的 migration 版本系统，替代 `CREATE TABLE IF NOT EXISTS`。
**Why:** 当前无法追踪数据库 schema 的变更历史，也无法可靠回滚。
**Pros:** schema 变更可追踪、可回滚、多开发者协作安全。
**Cons:** 引入 Alembic 有一定学习曲线，需要重新组织 model 定义。
**Context:** 当前用 `IF NOT EXISTS` + `IF NOT EXISTS ADD COLUMN` 在简单场景下够用，但项目可配置化后新增表和字段会更频繁。
**Effort:** M (human: ~2天 / CC: ~20分钟)
**Priority:** P2
**Depends on:** 无

---

## P1 — 数据库连接池健康检查

**What:** 给 database.py 连接池加 ping 检查和自动重连机制。
**Why:** PostgreSQL 重启后连接池中的旧连接变成死连接，所有后续操作 500 错误。
**Pros:** 系统自愈能力、减少运维干预。
**Cons:** 极小的性能开销（每次连接前多一次 ping）。
**Context:** Render 的 PostgreSQL 实例可能在维护窗口重启，这时 Web 和 Worker 服务都会受影响。
**Effort:** S (human: ~2小时 / CC: ~10分钟)
**Priority:** P1
**Depends on:** 无

---

## P1 — 邮件告警异步化 + 重试

**What:** 智能预警系统的邮件发送改为异步 + 失败重试 + 日志记录。
**Why:** SMTP 超时时告警静默丢失，用户不知道预警没发出去。这是 Critical Gap。
**Pros:** 告警可靠性、故障可追踪。
**Cons:** 需要引入简单的重试机制（指数退避）。
**Context:** 现有 `send_feedback_email()` 是同步调用，SMTP 超时会阻塞请求。预警系统复用此函数时，应改为后台线程发送 + 最多重试 3 次 + 每次失败记 logger.error。
**Effort:** S (human: ~2小时 / CC: ~10分钟)
**Priority:** P1
**Depends on:** 智能预警系统（提案 5）

---

生成时间: 2026-03-27
由 /plan-ceo-review CEO 评审 + /plan-eng-review 工程评审生成
