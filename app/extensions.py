from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect


csrf = CSRFProtect()
login_manager = LoginManager()
login_manager.login_view = "main.login"
login_manager.login_message = "Please sign in to continue."
login_manager.login_message_category = "info"
