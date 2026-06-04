# Sailson AI 工作台 - 核心功能保护清单

## 🔒 核心功能 - 禁止修改或删除

### 1. Apify 任务处理（舆情分析）
**文件**: `app.py`
**关键函数**:
- `process_analysis_task()` - 后台任务处理（行 359+）
- `analyze()` - 分析路由（行 780+）

**关键代码 - 禁止修改**:
```python
# ✅ 非守护线程（禁止改回 daemon=True）
thread = threading.Thread(...)
# 不设置 daemon=True

# ✅ 异常处理（禁止删除 try-except）
try:
    run = apify_client.run(run['id']).wait_for_finish(wait_secs=480)
except Exception as wait_error:
    # 错误处理逻辑

# ✅ 超时时间（禁止改回 180 秒）
wait_for_finish(wait_secs=480)  # 必须 >= 480
```

---

### 2. 竞品监控（异步处理）
**文件**: `app.py`
**关键函数**:
- `process_competitor_task()` - 后台任务处理（行 920+）
- `monitor_competitors()` - 竞品监控路由（行 874+）

**关键代码 - 禁止修改**:
```python
# ✅ 异步处理（禁止改回同步）
thread = threading.Thread(target=process_competitor_task, ...)
thread.start()
return jsonify({'task_id': task_id, ...})  # 立即返回

# ✅ 异常处理（禁止删除）
try:
    run = apify_client.run(run['id']).wait_for_finish(wait_secs=480)
except Exception as wait_error:
    # 错误处理逻辑
```

---

### 3. 数据库连接池
**文件**: `database.py`
**关键代码 - 禁止修改**:
```python
# ✅ 连接池（禁止改回直接连接）
from psycopg2 import pool

connection_pool = None

def init_connection_pool():
    global connection_pool
    connection_pool = psycopg2.pool.SimpleConnectionPool(
        minconn=1,
        maxconn=10,
        dsn=DATABASE_URL
    )

@contextmanager
def get_db_connection():
    conn = connection_pool.getconn()
    try:
        yield conn
        conn.commit()
    finally:
        connection_pool.putconn(conn)  # 必须释放连接
```

---

### 4. Apify 客户端初始化
**文件**: `app.py`
**关键代码 - 禁止修改**:
```python
# ✅ Token 验证（禁止删除）
if APIFY_TOKEN:
    try:
        apify_client = ApifyClient(APIFY_TOKEN)
        apify_client.user().get()  # 验证 Token
        logger.info("✅ Apify 客户端初始化成功，Token 有效")
    except Exception as e:
        logger.error(f"❌ Apify 客户端初始化失败或 Token 无效: {e}")
        apify_client = None
```

---

### 5. 任务恢复机制
**文件**: `app.py`
**关键函数**: `recover_interrupted_tasks()` (行 103+)

**关键代码 - 禁止修改**:
```python
# ✅ 应用启动时调用（禁止删除）
if __name__ == '__main__':
    recover_interrupted_tasks()  # 必须在启动时调用
    app.run(...)
```

---

### 6. Gunicorn 配置
**文件**: `gunicorn_config.py`
**关键配置 - 禁止修改**:
```python
# ✅ 单 worker（禁止增加 workers）
workers = 1

# ✅ 超时时间（禁止减少）
timeout = 600  # 必须 >= 600

# ✅ 优雅关闭
graceful_timeout = 120
```

---

## ✅ 可以安全修改的部分

### UI/前端修改
- ✅ HTML 模板 (`templates/*.html`)
- ✅ CSS 样式 (`static/` 目录)
- ✅ JavaScript 前端逻辑
- ✅ 页面布局和设计

### 用户管理功能
- ✅ 添加新的用户字段
- ✅ 修改用户权限逻辑
- ✅ 添加新的管理员功能
- ✅ 修改登录/注册流程

### 新增功能
- ✅ 添加新的路由和页面
- ✅ 添加新的数据库表
- ✅ 添加新的 API 端点
- ✅ 添加新的工具函数

### 优化改进
- ✅ 添加日志记录
- ✅ 改进错误提示
- ✅ 优化性能
- ✅ 添加缓存机制

---

## 🛡️ 修改前检查清单

在修改代码前，请确认：

1. **不涉及核心功能**
   - [ ] 不修改 `process_analysis_task()`
   - [ ] 不修改 `process_competitor_task()`
   - [ ] 不修改数据库连接池
   - [ ] 不修改 Apify 客户端初始化
   - [ ] 不修改线程创建逻辑

2. **不破坏异常处理**
   - [ ] 不删除 try-except 块
   - [ ] 不修改 `wait_for_finish()` 的异常处理
   - [ ] 不删除任务状态更新逻辑

3. **不修改关键配置**
   - [ ] 不将 `daemon=True` 加回去
   - [ ] 不减少超时时间（< 480 秒）
   - [ ] 不增加 Gunicorn workers
   - [ ] 不删除连接池

4. **测试验证**
   - [ ] 修改后运行 `python3 test_complete.py`
   - [ ] 确保所有测试通过
   - [ ] 在本地测试新功能
   - [ ] 部署前检查 Git diff

---

## 🚨 如果必须修改核心代码

如果确实需要修改核心功能，请遵循以下原则：

1. **先备份**
   ```bash
   git checkout -b backup-before-changes
   git push origin backup-before-changes
   ```

2. **咨询 Claude**
   - 说明修改原因
   - 描述期望效果
   - 让 Claude 评估风险

3. **保留核心逻辑**
   - 不删除异常处理
   - 不改变线程行为
   - 不破坏连接池

4. **完整测试**
   - 运行完整测试套件
   - 测试 Apify 任务
   - 验证数据库连接

---

## 📝 修改示例

### ✅ 安全修改示例

**修改 UI 颜色**:
```python
# templates/index.html
<style>
  .navbar { background-color: #2c3e50; }  /* 修改导航栏颜色 */
</style>
```

**添加新的用户字段**:
```python
# init_db.py
ALTER TABLE users ADD COLUMN phone VARCHAR(20);
```

**添加新的路由**:
```python
@app.route('/new-feature')
@login_required
def new_feature():
    return render_template('new_feature.html')
```

### ❌ 危险修改示例

**❌ 错误：改回守护线程**
```python
# 禁止这样做！
thread.daemon = True  # ❌ 会导致任务卡住
```

**❌ 错误：删除异常处理**
```python
# 禁止这样做！
run = apify_client.run(run['id']).wait_for_finish(wait_secs=480)
# ❌ 缺少 try-except，任务失败时无法更新状态
```

**❌ 错误：减少超时时间**
```python
# 禁止这样做！
wait_for_finish(wait_secs=180)  # ❌ 太短，任务会超时
```

**❌ 错误：改回同步处理**
```python
# 禁止这样做！
@app.route('/monitor_competitors', methods=['POST'])
def monitor_competitors():
    # ❌ 同步处理会阻塞主线程
    run = apify_client.run(run['id']).wait_for_finish(wait_secs=480)
    return jsonify({'result': result})
```

---

## 🔍 代码审查工具

我为你创建了一个自动检查脚本，在修改代码后运行：

```bash
python3 test_complete.py
```

这个脚本会验证：
- ✅ 语法正确性
- ✅ 核心函数存在
- ✅ 异常处理完整
- ✅ 超时配置正确
- ✅ 线程配置正确

---

## 📞 需要帮助时

如果你不确定某个修改是否安全，随时问我：

1. **描述你想做的修改**
   - 修改哪个文件
   - 修改什么功能
   - 期望达到什么效果

2. **我会评估**
   - 是否影响核心功能
   - 是否有风险
   - 如何安全实现

3. **提供安全方案**
   - 给出具体代码
   - 说明注意事项
   - 提供测试方法

---

## 🎯 总结

**核心原则**：
- 🔒 **核心功能** = 不可修改（Apify 任务、连接池、异常处理）
- ✅ **UI/用户管理** = 可以随意修改
- 🛡️ **修改前** = 先运行测试，确保不破坏核心功能
- 📞 **不确定** = 先问 Claude

**记住**：只要不碰核心的任务处理逻辑、数据库连接池、异常处理，你可以自由修改 UI 和添加新功能！

---

生成时间: 2026-02-25
版本: v1.0
