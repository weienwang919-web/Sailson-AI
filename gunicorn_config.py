# Gunicorn 配置文件
# 强制使用单个 worker，避免内存数据不共享的问题

import os

# 绑定地址
bind = f"0.0.0.0:{os.environ.get('PORT', 5001)}"

# 只使用 1 个 worker（重要！避免 TASK_QUEUE 不共享）
workers = 1

# 使用标准的同步 worker（与 threading 兼容）
worker_class = "sync"

# 每个 worker 的线程数
threads = 4

# 超时时间（秒）- 增加到 600 秒，因为爬虫需要时间
timeout = 600

# 日志配置
accesslog = "-"
errorlog = "-"
loglevel = "info"

# 优雅重启
graceful_timeout = 30
