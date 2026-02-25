"""
数据库连接和操作工具
"""
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager

DATABASE_URL = os.environ.get('DATABASE_URL')

# 修正 Render 的 postgres:// 为 postgresql://
if DATABASE_URL and DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

@contextmanager
def get_db_connection():
    """获取数据库连接（上下文管理器）"""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

@contextmanager
def get_db_cursor(commit=True):
    """获取数据库游标（上下文管理器）"""
    conn = psycopg2.connect(DATABASE_URL)
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
        conn.close()

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
