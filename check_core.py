#!/usr/bin/env python3
"""
核心功能保护检查器
在修改代码后运行此脚本，确保核心功能未被破坏
"""
import sys
import inspect

print("=" * 80)
print("🛡️  核心功能保护检查")
print("=" * 80)

errors = []
warnings = []

# 导入模块
try:
    import app
    import database
    print("✅ 模块导入成功")
except Exception as e:
    print(f"❌ 模块导入失败: {e}")
    sys.exit(1)

# 检查 1: 线程配置
print("\n🧵 检查 1: 线程配置")
analyze_source = inspect.getsource(app.analyze)
if 'daemon = True' in analyze_source or 'daemon=True' in analyze_source:
    errors.append("❌ 发现 daemon=True！这会导致任务被过早终止")
    print("   ❌ 发现 daemon=True")
else:
    print("   ✅ 线程配置正确（无 daemon=True）")

# 检查 2: 异常处理
print("\n🔒 检查 2: 异常处理")
process_task_source = inspect.getsource(app.process_analysis_task)

if 'wait_for_finish' in process_task_source:
    if 'try:' in process_task_source and 'except' in process_task_source:
        print("   ✅ wait_for_finish() 有异常处理")
    else:
        errors.append("❌ wait_for_finish() 缺少异常处理")
        print("   ❌ 缺少异常处理")
else:
    warnings.append("⚠️  未找到 wait_for_finish() 调用")
    print("   ⚠️  未找到 wait_for_finish()")

# 检查竞品监控异常处理
try:
    competitor_source = inspect.getsource(app.process_competitor_task)
    if 'wait_for_finish' in competitor_source:
        if 'try:' in competitor_source and 'except' in competitor_source:
            print("   ✅ 竞品监控有异常处理")
        else:
            errors.append("❌ 竞品监控缺少异常处理")
            print("   ❌ 竞品监控缺少异常处理")
except:
    errors.append("❌ process_competitor_task 函数不存在")
    print("   ❌ process_competitor_task 函数不存在")

# 检查 3: 超时时间
print("\n⏱️  检查 3: 超时时间配置")
if 'wait_secs=480' in process_task_source or 'wait_secs = 480' in process_task_source:
    print("   ✅ 舆情分析超时: 480 秒")
elif 'wait_secs=180' in process_task_source:
    errors.append("❌ 超时时间被改回 180 秒！应该是 480 秒")
    print("   ❌ 超时时间: 180 秒（太短！）")
else:
    warnings.append("⚠️  无法确定超时时间")
    print("   ⚠️  无法确定超时时间")

try:
    competitor_source = inspect.getsource(app.process_competitor_task)
    if 'wait_secs=480' in competitor_source or 'wait_secs = 480' in competitor_source:
        print("   ✅ 竞品监控超时: 480 秒")
    elif 'wait_secs=180' in competitor_source:
        errors.append("❌ 竞品监控超时被改回 180 秒")
        print("   ❌ 竞品监控超时: 180 秒（太短！）")
except:
    pass

# 检查 4: 数据库连接池
print("\n💾 检查 4: 数据库连接池")
try:
    from database import connection_pool, init_connection_pool
    print("   ✅ 连接池函数存在")

    db_source = inspect.getsource(database.get_db_connection)
    if 'getconn' in db_source and 'putconn' in db_source:
        print("   ✅ 连接池正确使用（getconn/putconn）")
    elif 'psycopg2.connect' in db_source and 'close()' in db_source:
        errors.append("❌ 数据库连接池被改回直接连接！")
        print("   ❌ 改回了直接连接（会导致连接耗尽）")
    else:
        warnings.append("⚠️  无法确定连接池实现")
        print("   ⚠️  无法确定连接池实现")
except ImportError:
    errors.append("❌ 连接池函数不存在")
    print("   ❌ 连接池函数不存在")

# 检查 5: Apify Token 验证
print("\n🔑 检查 5: Apify Token 验证")
app_source = inspect.getsource(app)
if 'apify_client.user().get()' in app_source:
    print("   ✅ Apify Token 验证存在")
else:
    warnings.append("⚠️  Apify Token 验证可能被删除")
    print("   ⚠️  Token 验证可能被删除")

# 检查 6: 任务恢复机制
print("\n🔄 检查 6: 任务恢复机制")
try:
    from app import recover_interrupted_tasks
    print("   ✅ recover_interrupted_tasks 函数存在")

    # 检查是否在启动时调用
    if 'recover_interrupted_tasks()' in app_source:
        print("   ✅ 启动时调用任务恢复")
    else:
        warnings.append("⚠️  启动时可能未调用任务恢复")
        print("   ⚠️  启动时可能未调用")
except ImportError:
    errors.append("❌ recover_interrupted_tasks 函数不存在")
    print("   ❌ 函数不存在")

# 检查 7: 竞品监控异步化
print("\n🎯 检查 7: 竞品监控异步化")
try:
    monitor_source = inspect.getsource(app.monitor_competitors)
    if 'threading.Thread' in monitor_source and 'return jsonify' in monitor_source:
        if 'task_id' in monitor_source:
            print("   ✅ 竞品监控是异步的（返回 task_id）")
        else:
            warnings.append("⚠️  竞品监控可能不是异步的")
            print("   ⚠️  可能不是异步的")
    else:
        errors.append("❌ 竞品监控被改回同步处理！")
        print("   ❌ 改回了同步处理（会阻塞主线程）")
except:
    errors.append("❌ monitor_competitors 函数不存在")
    print("   ❌ 函数不存在")

# 检查 8: Gunicorn 配置
print("\n⚙️  检查 8: Gunicorn 配置")
try:
    with open('gunicorn_config.py', 'r') as f:
        gunicorn_config = f.read()

    if 'workers = 1' in gunicorn_config or 'workers=1' in gunicorn_config:
        print("   ✅ Workers: 1（正确）")
    else:
        errors.append("❌ Workers 被修改！应该保持为 1")
        print("   ❌ Workers 不是 1")

    if 'timeout = 600' in gunicorn_config or 'timeout=600' in gunicorn_config:
        print("   ✅ Timeout: 600 秒（正确）")
    elif 'timeout' in gunicorn_config:
        warnings.append("⚠️  Timeout 可能被修改")
        print("   ⚠️  Timeout 可能被修改")
except FileNotFoundError:
    warnings.append("⚠️  gunicorn_config.py 不存在")
    print("   ⚠️  配置文件不存在")

# 总结
print("\n" + "=" * 80)
if errors:
    print("❌ 发现严重问题！")
    print("=" * 80)
    for error in errors:
        print(error)
    print("\n🚨 请立即修复这些问题，否则核心功能会被破坏！")
    sys.exit(1)
elif warnings:
    print("⚠️  发现警告")
    print("=" * 80)
    for warning in warnings:
        print(warning)
    print("\n💡 建议检查这些警告，但不影响核心功能")
    sys.exit(0)
else:
    print("✅ 所有检查通过！")
    print("=" * 80)
    print("\n🎉 核心功能完好无损，可以安全部署！")
    sys.exit(0)
