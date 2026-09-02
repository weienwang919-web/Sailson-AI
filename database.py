"""
数据库连接和操作工具
"""
import os
import time
import logging
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# 到 Render 数据库偶发网络抖动会在查询执行中把连接打断
# （psycopg2.OperationalError: SSL connection has been closed unexpectedly），
# 坏连接已经在 get_db_cursor() 里被报废不复用，这里再给只读查询加一层
# 指数退避重试（1s/2s/4s，最多3次），跟 tiktok_official_service._request()
# 对 TikTok API 429/5xx 的重试是同一个思路。只用于 query_*，不用于
# execute*——写操作重试有把同一条 INSERT/UPDATE 无意中跑两次的风险。
_READ_RETRY_DELAYS = (1, 2, 4)


def _with_read_retry(fn, *args, **kwargs):
    last_exc = None
    for attempt, delay in enumerate((0,) + _READ_RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            return fn(*args, **kwargs)
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            last_exc = e
            logger.warning(f"只读查询遇到连接异常，第 {attempt + 1} 次尝试失败: {e}")
    raise last_exc

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
            connection_pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=1,
                # worker 并发提到 8 之后，10 个连接会成为新瓶颈（线程排队等连接）。
                # Postgres 那边 max_connections=103，web+worker 各 20 仍有充裕余量。
                maxconn=int(os.environ.get('DB_POOL_MAX', '20')),
                dsn=DATABASE_URL
            )
            logger.info("✅ 数据库连接池初始化成功")
        except Exception as e:
            logger.error(f"❌ 数据库连接池初始化失败: {e}")
            raise

@contextmanager
def get_db_connection():
    """获取数据库连接（上下文管理器）。

    出错时能不能简单 rollback 复用连接，还是必须整条连接报废重开，
    只有实际尝试 rollback 才知道——语句超时（statement_timeout）之类的
    OperationalError 一次 rollback 就能恢复，但网络断开/服务端主动断连后
    再 rollback 会再报一次错，这时才需要把连接标记为坏连接、整条关掉，
    否则它会一直卡在池子里，下一个借到它的请求立刻收到
    「connection already closed」，直到进程重启才会消失。
    """
    if not connection_pool:
        init_connection_pool()

    conn = connection_pool.getconn()
    broken = False
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            broken = True
        raise
    finally:
        broken = broken or conn.closed
        connection_pool.putconn(conn, close=broken)

@contextmanager
def get_db_cursor(commit=True):
    """获取数据库游标（上下文管理器），坏连接判定同 get_db_connection()。"""
    if not connection_pool:
        init_connection_pool()

    conn = connection_pool.getconn()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    broken = False
    try:
        yield cursor
        if commit:
            conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            broken = True
        raise
    finally:
        cursor.close()
        broken = broken or conn.closed
        connection_pool.putconn(conn, close=broken)

def _query_one(sql, params=None):
    with get_db_cursor(commit=False) as cursor:
        cursor.execute(sql, params or ())
        return cursor.fetchone()

def _query_all(sql, params=None):
    with get_db_cursor(commit=False) as cursor:
        cursor.execute(sql, params or ())
        return cursor.fetchall()

def _query_one_with_timeout(sql, params=None, timeout='55s'):
    with get_db_cursor(commit=False) as cursor:
        cursor.execute(f"SET LOCAL statement_timeout = '{timeout}'")
        cursor.execute(sql, params or ())
        return cursor.fetchone()

def _query_all_with_timeout(sql, params=None, timeout='55s'):
    with get_db_cursor(commit=False) as cursor:
        cursor.execute(f"SET LOCAL statement_timeout = '{timeout}'")
        cursor.execute(sql, params or ())
        return cursor.fetchall()

def query_one(sql, params=None):
    """查询单条记录"""
    return _with_read_retry(_query_one, sql, params)

def query_all(sql, params=None):
    """查询多条记录"""
    return _with_read_retry(_query_all, sql, params)

def query_one_with_timeout(sql, params=None, timeout='55s'):
    """查询单条记录（带 statement_timeout，防止慢查询拖垮 Render 代理）"""
    return _with_read_retry(_query_one_with_timeout, sql, params, timeout)

def query_all_with_timeout(sql, params=None, timeout='55s'):
    """查询多条记录（带 statement_timeout，防止慢查询拖垮 Render 代理）"""
    return _with_read_retry(_query_all_with_timeout, sql, params, timeout)

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

def execute_and_fetch_one(sql, params=None):
    """执行带 RETURNING 的 UPDATE/INSERT 并返回一行结果（带 commit）"""
    with get_db_cursor(commit=True) as cursor:
        cursor.execute(sql, params or ())
        return cursor.fetchone()
