"""SIA — database engine + session helpers (SQLite, WAL mode)."""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from backend.core.config import settings
from backend.database.models import Base

_engine = None
_Session: sessionmaker | None = None


def _make_engine(path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    eng = create_engine(f"sqlite:///{path}", future=True,
                        connect_args={"check_same_thread": False, "timeout": 15})

    @event.listens_for(eng, "connect")
    def _pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
    return eng


def init_db(path: str | None = None) -> None:
    """Create tables (idempotent). Pass a path to use a different DB (tests)."""
    global _engine, _Session
    _engine = _make_engine(path or settings.DB_PATH)
    Base.metadata.create_all(_engine)
    _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    if _Session is None:
        init_db()
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def to_json(obj) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return json.dumps(str(obj))
