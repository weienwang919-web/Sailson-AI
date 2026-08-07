# Gunicorn 配置文件
# 强制使用单个 worker，避免内存数据不共享的问题

import os

# 绑定地址
bind = f"0.0.0.0:{os.environ.get('PORT', 5001)}"

# 只使用 1 个 worker（重要！避免 TASK_QUEUE 不共享）
workers = 1

# gthread：进程内多线程并发处理请求，内存态数据仍然共享（同一个进程）；
# 之前用 sync + threads=1 导致同一时刻只能处理一个请求，
# 一旦有慢请求占住了唯一的处理槽位，Render 的健康检查探针就会排队超时，
# 被误判为实例挂了从而重启。
worker_class = "gthread"

# 每个 worker 的线程数：从 1 调到 3，留出并发余量给健康检查探针
threads = 3

# 超时时间（秒）- 增加到 600 秒，因为爬虫需要时间
timeout = 600

# 优雅关闭超时 - 给正在执行的任务足够时间完成
graceful_timeout = 120

# 保持连接时间
keepalive = 5

# 日志配置
accesslog = "-"
errorlog = "-"
loglevel = "info"

# 预加载应用已禁用 - 避免 Apify 客户端在 fork 后失效
# preload_app = True
