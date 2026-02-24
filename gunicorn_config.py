# Gunicorn 配置文件 - 针对 Render 云端优化

import os

# 绑定地址和端口
bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"

# Worker 配置
workers = 1  # Render 免费套餐内存有限，使用单个 worker
worker_class = "sync"  # 使用同步 worker
threads = 2  # 每个 worker 使用 2 个线程

# 超时设置（AI API 调用可能需要较长时间）
timeout = 300  # 5 分钟超时（原默认 30 秒）
graceful_timeout = 30
keepalive = 5

# 内存管理
max_requests = 100  # 处理 100 个请求后重启 worker，防止内存泄漏
max_requests_jitter = 10

# 日志配置
accesslog = "-"  # 输出到 stdout
errorlog = "-"   # 输出到 stderr
loglevel = "info"

# 进程命名
proc_name = "sailson-ai"

# 预加载应用（节省内存）
preload_app = True
