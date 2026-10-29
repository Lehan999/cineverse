import os

from flask import Flask, render_template
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
from flask_wtf.csrf import CSRFError

from app.extensions import csrf, login_manager

load_dotenv()

db=SQLAlchemy()

def create_app(test_config=None):
    app = Flask(__name__)

    app.config.from_mapping(
        SECRET_KEY=os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY") or os.urandom(32),
        SQLALCHEMY_DATABASE_URI="sqlite:///Cineverse.db",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        WTF_CSRF_ENABLED=True,
    )
    if test_config:
        app.config.update(test_config)

    db.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)

    from app.models import User

    from app.routes import main
    app.register_blueprint(main)

    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(User, int(user_id))
        except (TypeError, ValueError):
            return None

    # create_all only adds missing tables; it does not reset existing movie data.
    with app.app_context():
        db.create_all()

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        return render_template("error.html", message="This form expired or could not be verified. Refresh the page and try again."), 400

    return app
