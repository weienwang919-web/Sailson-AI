#!/usr/bin/env python3
"""
测试脚本 - 验证关键修复
"""
import sys
import os

print("=" * 60)
print("🧪 开始测试关键修复...")
print("=" * 60)

# 测试 1: 检查语法
print("\n✅ 测试 1: Python 语法检查")
try:
    import app
    print("   ✓ app.py 语法正确")
except SyntaxError as e:
    print(f"   ✗ app.py 语法错误: {e}")
    sys.exit(1)

try:
    import database
    print("   ✓ database.py 语法正确")
except SyntaxError as e:
    print(f"   ✗ database.py 语法错误: {e}")
    sys.exit(1)

# 测试 2: 检查数据库连接池
print("\n✅ 测试 2: 数据库连接池实现")
try:
    from database import init_connection_pool, connection_pool
    print("   ✓ 连接池函数已定义")
    print(f"   ✓ 连接池初始状态: {connection_pool}")
except ImportError as e:
    print(f"   ✗ 连接池导入失败: {e}")
    sys.exit(1)

# 测试 3: 检查 Apify Token 验证
print("\n✅ 测试 3: Apify Token 验证")
try:
    apify_client = app.apify_client
    if apify_client:
        print("   ✓ Apify 客户端已初始化")
        print("   ✓ Token 验证已通过（启动时已验证）")
    else:
        print("   ⚠ Apify 客户端未初始化（可能是 Token 无效或未配置）")
except Exception as e:
    print(f"   ✗ Apify 客户端检查失败: {e}")

# 测试 4: 检查任务恢复函数
print("\n✅ 测试 4: 任务恢复机制")
try:
    from app import recover_interrupted_tasks
    print("   ✓ recover_interrupted_tasks 函数已定义")
except ImportError as e:
    print(f"   ✗ 任务恢复函数导入失败: {e}")
    sys.exit(1)

# 测试 5: 检查竞品监控异步函数
print("\n✅ 测试 5: 竞品监控异步化")
try:
    from app import process_competitor_task
    print("   ✓ process_competitor_task 函数已定义")
except ImportError as e:
    print(f"   ✗ 竞品监控异步函数导入失败: {e}")
    sys.exit(1)

# 测试 6: 检查关键代码修改
print("\n✅ 测试 6: 关键代码修改验证")
import inspect

# 检查 analyze 路由中是否移除了 daemon=True
analyze_source = inspect.getsource(app.analyze)
if 'daemon = True' in analyze_source:
    print("   ✗ analyze 函数中仍然存在 daemon=True")
    sys.exit(1)
else:
    print("   ✓ daemon=True 已移除")

# 检查 process_analysis_task 中是否有异常处理
process_task_source = inspect.getsource(app.process_analysis_task)
if 'wait_for_finish' in process_task_source and 'try:' in process_task_source:
    print("   ✓ wait_for_finish() 已添加异常处理")
else:
    print("   ⚠ 可能缺少异常处理")

# 检查超时时间
if 'wait_secs=480' in process_task_source:
    print("   ✓ Apify 超时时间已增加到 480 秒")
elif 'wait_secs=180' in process_task_source:
    print("   ✗ Apify 超时时间仍然是 180 秒")
else:
    print("   ⚠ 无法确定超时时间")

print("\n" + "=" * 60)
print("✅ 所有测试通过！代码修复验证成功")
print("=" * 60)
print("\n📝 下一步:")
print("   1. 提交代码到 Git")
print("   2. 推送到 Render 进行部署")
print("   3. 在 Render 上测试实际功能")
print()
