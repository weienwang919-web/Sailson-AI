# Gunicorn 配置文件（render.yaml / start_render.sh 使用：gunicorn app:app -c gunicorn_config.py）
#
# 注意：线上 Render Dashboard 里配置的 Start Command 会覆盖 render.yaml，
# 如果 Dashboard 用的是 `gunicorn wsgi:app --workers ... --threads ...`，以命令行参数为准。
#
# 以前强制 1 个 worker（担心内存 TASK_QUEUE 不共享）。任务队列早已迁到数据库
# （USE_DB_WORKER=true，由独立的 worker 服务执行），APScheduler / KOL 子进程
# 也已在 app.py 里用文件锁保证整台实例只启动一份，所以可以安全地开多 worker。
# 之前 1 个 sync worker + 1 线程，一个慢请求（如 26MB 的 /get_history）就会让
# Render 的 /health 探针（5 秒超时）失败，实例被判定不健康而下线。

import os

# 绑定端口
bind = f"0.0.0.0:{os.environ.get('PORT', 5001)}"

# 进程数：可用 WEB_CONCURRENCY 环境变量覆盖
workers = int(os.environ.get('WEB_CONCURRENCY', '2'))

# gthread：每个 worker 多线程处理请求，慢请求不会堵住健康检查
worker_class = "gthread"
threads = int(os.environ.get('GUNICORN_THREADS', '8'))

# 超时（秒）：部分同步接口仍可能较慢
timeout = 600

# 优雅关闭超时 - 给正在执行的请求足够时间完成
graceful_timeout = 120

# 保持连接时间
keepalive = 5

# 日志
accesslog = "-"
errorlog = "-"
loglevel = "info"

# 预加载已禁用 - 避免 Apify 客户端 / 数据库连接池在 fork 后失效
# preload_app = True
