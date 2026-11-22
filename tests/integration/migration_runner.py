"""Runs one Alembic action against a SQLite file, in its own process.

Usage: python tests/integration/migration_runner.py <db-file> <stamp|upgrade|downgrade> <revision>

A subprocess is used because Alembic's env.py reconfigures logging, which
would otherwise leak into the rest of the test session.
"""
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("DATABASE_NEW_URL", "sqlite:///:memory:")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
os.environ.setdefault("REDIS_URL", "redis://localhost:6399/0")

from flask import Flask                                  # noqa: E402
from flask_migrate import Migrate, downgrade, stamp, upgrade  # noqa: E402

from extensions import db                                # noqa: E402

db_file, action, revision = sys.argv[1:4]
app = Flask(__name__)
app.config.update(SQLALCHEMY_DATABASE_URI=f"sqlite:///{db_file}", SQLALCHEMY_TRACK_MODIFICATIONS=False)
db.init_app(app)
Migrate(app, db, directory=str(ROOT / "migrations"))

with app.app_context():
    {"stamp": stamp, "upgrade": upgrade, "downgrade": downgrade}[action](
        directory=str(ROOT / "migrations"), revision=revision)
