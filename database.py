"""
数据库连接和操作工具
"""
import os
import logging
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get('DATABASE_URL')

# 修正 Render 的 postgres:// 为 postgresql://
if DATABASE_URL and DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

# 全局连接池
connection_pool = None

def init_connection_pool():
    """初始化数据库连接池"""
    global connection_pool
    if not connection_pool:
        try:
            connection_pool = psycopg2.pool.SimpleConnectionPool(
                minconn=1,
                maxconn=10,
                dsn=DATABASE_URL
            )
            logger.info("✅ 数据库连接池初始化成功")
        except Exception as e:
            logger.error(f"❌ 数据库连接池初始化失败: {e}")
            raise

@contextmanager
def get_db_connection():
    """获取数据库连接（上下文管理器）"""
    if not connection_pool:
        init_connection_pool()

    conn = connection_pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        connection_pool.putconn(conn)

@contextmanager
def get_db_cursor(commit=True):
    """获取数据库游标（上下文管理器）"""
    if not connection_pool:
        init_connection_pool()

    conn = connection_pool.getconn()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        yield cursor
        if commit:
            conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        connection_pool.putconn(conn)

def query_one(sql, params=None):
    """查询单条记录"""
    with get_db_cursor(commit=False) as cursor:
        cursor.execute(sql, params or ())
        return cursor.fetchone()

def query_all(sql, params=None):
    """查询多条记录"""
    with get_db_cursor(commit=False) as cursor:
        cursor.execute(sql, params or ())
        return cursor.fetchall()

def execute(sql, params=None):
    """执行 SQL（INSERT/UPDATE/DELETE）"""
    with get_db_cursor(commit=True) as cursor:
        cursor.execute(sql, params or ())
        return cursor.rowcount

def execute_and_fetch_id(sql, params=None):
    """执行 SQL 并返回插入的 ID"""
    with get_db_cursor(commit=True) as cursor:
        cursor.execute(sql, params or ())
        result = cursor.fetchone()
        return result['id'] if result else None
