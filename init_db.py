"""
数据库初始化脚本
"""
import os
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from flask_bcrypt import Bcrypt
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

bcrypt = Bcrypt()

# 从环境变量获取数据库连接信息
DATABASE_URL = os.environ.get('DATABASE_URL')

if not DATABASE_URL:
    print("❌ 错误：未找到 DATABASE_URL 环境变量")
    print("请在 .env 文件中添加：DATABASE_URL=postgresql://...")
    exit(1)

# 修正 Render 的 postgres:// 为 postgresql://
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

print("=" * 60)
print("🗄️ 开始初始化数据库...")
print("=" * 60)

try:
    # 连接数据库
    conn = psycopg2.connect(DATABASE_URL)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cursor = conn.cursor()

    print("✅ 数据库连接成功")

    # 创建用户表
    print("\n📋 创建用户表...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(50) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            real_name VARCHAR(100) NOT NULL,
            department VARCHAR(50) NOT NULL,
            role VARCHAR(20) NOT NULL DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    print("✅ 用户表创建成功")

    # 创建使用记录表
    print("\n📋 创建使用记录表...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usage_logs (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            username VARCHAR(50) NOT NULL,
            department VARCHAR(50) NOT NULL,
            function_type VARCHAR(50) NOT NULL,
            comments_count INTEGER DEFAULT 0,
            ai_tokens INTEGER DEFAULT 0,
            ai_cost DECIMAL(10, 4) DEFAULT 0,
            apify_cost DECIMAL(10, 4) DEFAULT 0,
            total_cost DECIMAL(10, 4) DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    print("✅ 使用记录表创建成功")

    print("\n📋 创建统一消耗明细表...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usage_events (
            id SERIAL PRIMARY KEY,
            event_key VARCHAR(160) UNIQUE,
            source VARCHAR(32) NOT NULL DEFAULT 'actual',
            module VARCHAR(80) NOT NULL,
            task_id VARCHAR(128),
            record_id INTEGER,
            user_id INTEGER REFERENCES users(id),
            username VARCHAR(80),
            department VARCHAR(80),
            item_count INTEGER DEFAULT 0,
            crawler_items INTEGER DEFAULT 0,
            ai_tokens INTEGER DEFAULT 0,
            api_calls INTEGER DEFAULT 0,
            crawler_cost_usd DECIMAL(12, 4) DEFAULT 0,
            crawler_cost_cny DECIMAL(12, 4) DEFAULT 0,
            ai_cost_cny DECIMAL(12, 4) DEFAULT 0,
            total_cost_cny DECIMAL(12, 4) DEFAULT 0,
            pricing_json TEXT,
            detail_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    print("✅ 统一消耗明细表创建成功")

    # 创建分析结果表
    print("\n📋 创建分析结果表...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS analysis_results (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            title VARCHAR(255) NOT NULL,
            result TEXT,
            result_json TEXT,
            type VARCHAR(50) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    print("✅ 分析结果表创建成功")

    # 创建任务队列表
    print("\n📋 创建任务队列表...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS task_queue (
            task_id VARCHAR(100) PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            status VARCHAR(20) NOT NULL,
            progress TEXT,
            result TEXT,
            error TEXT,
            session_id VARCHAR(100),
            function_type VARCHAR(50),
            record_id INTEGER,
            task_params TEXT,
            worker_id VARCHAR(128),
            started_at TIMESTAMP,
            finished_at TIMESTAMP,
            attempts INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    print("✅ 任务队列表创建成功")

    # 检查是否已存在管理员账号
    cursor.execute("SELECT COUNT(*) FROM users WHERE username = 'admin'")
    admin_exists = cursor.fetchone()[0] > 0

    if not admin_exists:
        # 创建初始管理员账号
        print("\n👤 创建初始管理员账号...")
        password_hash = bcrypt.generate_password_hash('Admin@123').decode('utf-8')
        cursor.execute("""
            INSERT INTO users (username, password_hash, real_name, department, role)
            VALUES (%s, %s, %s, %s, %s)
        """, ('admin', password_hash, '系统管理员', '管理层', 'admin'))
        print("✅ 管理员账号创建成功")
        print("   用户名: admin")
        print("   密码: Admin@123")
    else:
        print("\n⚠️ 管理员账号已存在，跳过创建")

    # 创建索引以提升查询性能
    print("\n📊 创建索引...")
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_usage_logs_user_id ON usage_logs(user_id);
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_usage_logs_created_at ON usage_logs(created_at);
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_usage_events_created_at ON usage_events(created_at);
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_usage_events_user_id ON usage_events(user_id);
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_usage_events_module ON usage_events(module);
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_analysis_results_user_id ON analysis_results(user_id);
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_task_queue_user_id ON task_queue(user_id);
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_task_queue_created_at ON task_queue(created_at);
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_task_queue_status_created ON task_queue(status, created_at);
    """)
    print("✅ 索引创建成功")

    cursor.close()
    conn.close()

    print("\n" + "=" * 60)
    print("🎉 数据库初始化完成！")
    print("=" * 60)

except Exception as e:
    print(f"\n❌ 数据库初始化失败: {e}")
    import traceback
    traceback.print_exc()
    exit(1)
