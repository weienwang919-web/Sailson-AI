# FB 抓取任务改进 - 完整解决方案

## 问题分析

### 原始问题
1. **超时问题**：Apify 抓取需要 1-3 分钟，HTTP 请求 60 秒超时
2. **后台线程不可靠**：Gunicorn worker 重启时会杀掉后台线程
3. **无状态跟踪**：用户不知道任务是否在运行，运行到哪一步
4. **前端显示错误**：显示"undefined 条评论"

## 完整解决方案

### 1. 数据库任务状态表
新增 `scrape_tasks` 表：
- `id` - 任务 ID
- `task_type` - 任务类型（fb_scrape/tiktok_hotspot）
- `status` - 状态（pending/running/completed/failed）
- `started_at` - 开始时间
- `completed_at` - 完成时间
- `result_summary` - 结果摘要
- `error_message` - 错误信息

### 2. 任务生命周期管理

**创建任务**：
```python
task_id = db.execute_and_fetch_id(
    "INSERT INTO scrape_tasks (task_type, status) VALUES (%s, %s) RETURNING id",
    ('fb_scrape', 'pending')
)
```

**更新状态**：
- 开始时：`status = 'running'`
- 成功时：`status = 'completed'`, 记录 `result_summary`
- 失败时：`status = 'failed'`, 记录 `error_message`

### 3. 后台任务执行
```python
def run_scrape():
    try:
        result = tasks.scrape_fb_comments(task_id=task_id)
        # 任务内部会更新状态
    except Exception as e:
        # 捕获异常并记录到数据库
        db.execute("UPDATE scrape_tasks SET status = 'failed', error_message = %s ...")
```

### 4. 前端状态轮询

**工作流程**：
1. 用户点击"手动抓取"
2. 后端创建任务记录，返回 `task_id`
3. 前端开始轮询任务状态（每 5 秒一次）
4. 任务完成后显示结果并刷新数据

**轮询逻辑**：
- 最多轮询 60 次（5 分钟）
- 状态为 `completed` → 显示成功，刷新数据
- 状态为 `failed` → 显示错误信息
- 超时 → 提示用户手动刷新

### 5. 新增 API 接口

**POST /fb_schedule**
- 创建任务记录
- 启动后台线程
- 返回 `task_id`

**GET /fb_task_status/<task_id>**
- 查询任务状态
- 返回状态、时间、结果摘要

## 优势

### 可靠性
✅ 任务状态持久化到数据库
✅ Worker 重启不影响状态查询
✅ 完整的错误处理和日志

### 用户体验
✅ 实时状态反馈
✅ 自动轮询，无需手动刷新
✅ 清晰的成功/失败提示
✅ 按钮状态管理（禁用/启用）

### 可扩展性
✅ 支持多种任务类型
✅ 可以添加任务队列
✅ 可以添加任务历史查询

## 工作流程

```
用户点击"手动抓取"
    ↓
创建任务记录 (status=pending)
    ↓
启动后台线程
    ↓
立即返回 task_id
    ↓
前端开始轮询 (每5秒)
    ↓
后台任务执行中 (status=running)
    ↓
Apify 抓取评论 (1-3分钟)
    ↓
分析情感 + 生成 embedding
    ↓
存入数据库
    ↓
更新任务状态 (status=completed)
    ↓
前端检测到完成
    ↓
显示结果 + 刷新数据
```

## 容错机制

1. **Apify 超时**：设置 5 分钟超时
2. **数据库错误**：捕获异常，记录日志
3. **Worker 重启**：任务状态保留在数据库
4. **前端超时**：最多轮询 5 分钟后提示用户

## 测试要点

- [ ] 任务创建成功
- [ ] 状态正确更新（pending → running → completed）
- [ ] 前端轮询正常工作
- [ ] 成功时显示结果摘要
- [ ] 失败时显示错误信息
- [ ] 按钮状态正确切换
- [ ] 数据���动刷新
- [ ] Worker 重启后状态仍可查询

## 部署注意事项

1. 数据库表会自动创建（`rag.ensure_tables()`）
2. 无需额外配置
3. 兼容现有功能
4. 向后兼容（不影响定时任务）
