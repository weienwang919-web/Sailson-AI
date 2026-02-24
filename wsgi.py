"""
WSGI 入口文件 - 用于 Gunicorn 启动
"""
import sys
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

logger.info("=" * 60)
logger.info("🔧 WSGI 入口启动中...")
logger.info("=" * 60)

try:
    from app import app
    logger.info("✅ Flask 应用导入成功")
except Exception as e:
    logger.error(f"❌ Flask 应用导入失败: {e}")
    import traceback
    traceback.print_exc()
    raise

if __name__ == "__main__":
    app.run()
