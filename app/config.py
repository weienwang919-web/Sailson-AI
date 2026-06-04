"""
集中管理所有环境变量和常量配置。

其他模块统一从此处导入，避免到处 os.environ.get()。
"""
import os
import sys
import logging
from dotenv import load_dotenv

load_dotenv()

# ============================================
# 日志配置
# ============================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger('sailson')

# ============================================
# 清除代理设置（云端环境不需要代理）
# ============================================
for proxy_var in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']:
    if proxy_var in os.environ:
        del os.environ[proxy_var]
        logger.info(f"🧹 已清除代理设置: {proxy_var}")

# ============================================
# 核心环境变量
# ============================================
DASHSCOPE_API_KEY = os.environ.get('DASHSCOPE_API_KEY')
APIFY_TOKEN = os.environ.get('APIFY_TOKEN')
PORT = int(os.environ.get('PORT', 5001))

# 长任务处理模式
USE_DB_WORKER = os.environ.get('USE_DB_WORKER', 'false').lower() == 'true'

# Worker 进程标记
IS_WORKER = os.environ.get('_IS_WORKER', 'false').lower() == 'true'

# ============================================
# SMTP 邮件配置（可选）
# ============================================
SMTP_HOST = os.environ.get('SMTP_HOST')
SMTP_PORT = int(os.environ.get('SMTP_PORT', '587'))
SMTP_USER = os.environ.get('SMTP_USER')
SMTP_PASS = os.environ.get('SMTP_PASS')
FEEDBACK_EMAIL_TO = os.environ.get('FEEDBACK_EMAIL_TO')
FEEDBACK_EMAIL_FROM = os.environ.get('FEEDBACK_EMAIL_FROM', SMTP_USER or FEEDBACK_EMAIL_TO or '')

# ============================================
# 业务常量
# ============================================
VALID_PROJECTS = ('CFL', 'PUBGM', 'HOK')
USD_TO_CNY = 7.2

# ============================================
# XSS 防护：报告 HTML 允许的标签与属性
# ============================================
ALLOWED_TAGS = {
    'div', 'span', 'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'table', 'thead', 'tbody', 'tr', 'th', 'td',
    'ul', 'ol', 'li', 'br', 'strong', 'em', 'b', 'i',
    'a', 'img', 'blockquote', 'code', 'pre'
}
ALLOWED_ATTRS = {
    '*': ['class', 'style', 'id'],
    'a': ['href', 'title', 'target'],
    'img': ['src', 'alt', 'width', 'height'],
    'td': ['colspan', 'rowspan'],
    'th': ['colspan', 'rowspan'],
}
ALLOWED_PROTOCOLS = ['http', 'https', 'data', 'mailto']
