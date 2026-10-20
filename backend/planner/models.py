"""Database tables. SQLite locally, Postgres when hosted (set DATABASE_URL)."""
from __future__ import annotations

import os
from datetime import datetime

from sqlalchemy import (Boolean, DateTime, ForeignKey, Integer, String, Text,
                        create_engine, event, inspect, text)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    course: Mapped[str | None] = mapped_column(String(50))
    due: Mapped[datetime] = mapped_column(DateTime)
    est_minutes: Mapped[int] = mapped_column(Integer, default=60)
    done_minutes: Mapped[int] = mapped_column(Integer, default=0)
    min_total_minutes: Mapped[int | None] = mapped_column(Integer)  # squeeze floor; None = not compressible
    priority: Mapped[int] = mapped_column(Integer, default=2)        # 1 high .. 3 low
    splittable: Mapped[bool] = mapped_column(Boolean, default=True)
    min_block: Mapped[int] = mapped_column(Integer, default=30)
    max_block: Mapped[int] = mapped_column(Integer, default=180)
    tags: Mapped[str] = mapped_column(String(200), default="")       # comma separated, no '#'
    earliest_start: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | done
    source: Mapped[str] = mapped_column(String(50), default="manual")  # manual | google_tasks | scrape:csse304 ...
    external_id: Mapped[str | None] = mapped_column(String(300), unique=True)
    needs_estimate: Mapped[bool] = mapped_column(Boolean, default=False)  # imported with a default duration
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    # Subtasks: a task with a parent is a step of that parent. Parents with open subtasks
    # aren't scheduled themselves; their steps are (in order, if `sequential`).
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer, default=0)
    sequential: Mapped[bool] = mapped_column(Boolean, default=True)

    @property
    def tag_list(self) -> list[str]:
        return [t.strip().lstrip("#").lower() for t in self.tags.split(",") if t.strip()]


class Routine(Base):
    """Recurring commitment placed inside a window: meals, gym, chores, or fixed classes
    (a fixed class is just a routine whose window equals its duration)."""
    __tablename__ = "routines"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    days: Mapped[str] = mapped_column(String(7))           # subset of MTWRFSU
    window_start: Mapped[str] = mapped_column(String(5))   # "HH:MM"
    window_end: Mapped[str] = mapped_column(String(5))
    duration: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20), default="routine")  # meal | gym | chore | class | routine
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Rule(Base):
    """Deadline rule: tasks tagged `tag` must be finished by `weekday` `time` of their due week."""
    __tablename__ = "rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    tag: Mapped[str] = mapped_column(String(50))
    weekday: Mapped[int] = mapped_column(Integer)   # 0 = Monday
    time: Mapped[str] = mapped_column(String(5))    # "HH:MM"
    description: Mapped[str] = mapped_column(String(300), default="")


class Block(Base):
    """A scheduled time block. Planned blocks are regenerated on every replan unless
    locked (moved by hand), already checked in, or in the past."""
    __tablename__ = "blocks"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    start: Mapped[datetime] = mapped_column(DateTime)
    end: Mapped[datetime] = mapped_column(DateTime)
    kind: Mapped[str] = mapped_column(String(20))   # task | routine | busy
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    routine_id: Mapped[int | None] = mapped_column(ForeignKey("routines.id", ondelete="CASCADE"))
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="planned")  # planned | done | partial | skipped
    done_minutes: Mapped[int] = mapped_column(Integer, default=0)
    gcal_event_id: Mapped[str | None] = mapped_column(String(300))
    gcal_updated: Mapped[str | None] = mapped_column(String(64))


class ExternalEvent(Base):
    """Busy time imported from Google Calendar (classes, meetings...)."""
    __tablename__ = "external_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    gcal_id: Mapped[str] = mapped_column(String(300), unique=True)
    calendar_id: Mapped[str] = mapped_column(String(300))
    title: Mapped[str] = mapped_column(String(300))
    start: Mapped[datetime] = mapped_column(DateTime)
    end: Mapped[datetime] = mapped_column(DateTime)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class OAuthToken(Base):
    __tablename__ = "oauth_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(300), unique=True)
    token_json: Mapped[str] = mapped_column(Text)


DEFAULT_SETTINGS = {
    "timezone": "America/New_York",
    "day_start": "08:00",
    "day_end": "23:00",
    "horizon_days": "14",
    "plan_calendar_id": "",
    "busy_calendar_ids": "primary",
    "class_start_bc": "10:00",   # "BC:" (beginning of class) items on course sites
}


def make_session_factory(url: str | None = None):
    url = url or os.getenv("DATABASE_URL", "sqlite:///planner.db")
    if url.startswith("postgres://"):  # Render/Heroku style URLs
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    
    if url.startswith("sqlite"):
            engine = create_engine(url, future=True)
    else:
        engine = create_engine(url, future=True, pool_pre_ping=True, pool_recycle=240)
        
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def _fk_on(conn, _):  # SQLite ignores ON DELETE CASCADE unless this is on
            conn.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _add_missing_columns(engine) -> None:
    """Tiny migration step: add columns that were introduced after a database was created."""
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl_type = col.type.compile(engine.dialect)
                default = ""
                if col.default is not None and not callable(col.default.arg):
                    v = col.default.arg
                    default = f" DEFAULT {int(v) if isinstance(v, bool) else repr(v)}"
                if isinstance(col.type, Boolean) and engine.dialect.name == "postgresql" and default:
                    default = f" DEFAULT {'TRUE' if col.default.arg else 'FALSE'}"
                conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl_type}{default}'))
