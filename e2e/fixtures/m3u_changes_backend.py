"""Private browser-test server: production Changes routes and real SQLite.

No main-app scheduler, upstream connections, or live configuration. Ancillary
app-shell APIs are routed by the browser fixture, not served here.
"""
from datetime import datetime
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import uvicorn

import auth.dependencies
import database
from db_base import Base
from models import M3UChangeLog
from routers import m3u_digest

directory = TemporaryDirectory(prefix="z6jtf-browser-")
engine = create_engine(f"sqlite:///{directory.name}/journal.db", connect_args={"check_same_thread": False})
Base.metadata.create_all(engine)
factory = sessionmaker(bind=engine)
m3u_digest.get_session = factory
auth.dependencies.get_auth_settings = lambda: SimpleNamespace(require_auth=False, setup_complete=True)
with factory() as session:
    for account, name in [(1, "Sports 🏈"), (1, "News"), (2, "Sports 🏈")]:
        session.add(M3UChangeLog(
            m3u_account_id=account, group_name=name, change_type="streams_added",
            count=3, enabled=True, change_time=datetime.utcnow(),
        ))
    session.commit()

app = FastAPI()


def private_session():
    with factory() as session:
        yield session


app.dependency_overrides[database.get_session] = private_session
app.include_router(m3u_digest.router)

if __name__ == "__main__":
    try:
        uvicorn.run(app, host="127.0.0.1", port=42872)
    finally:
        engine.dispose()
        directory.cleanup()
