"""
Flask 扩展实例 — 在 create_app() 中通过 init_app() 绑定到 app。

其他模块需要 bcrypt 时从这里导入，避免循环依赖。
"""
from flask_bcrypt import Bcrypt

bcrypt = Bcrypt()
