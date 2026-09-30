from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session
import os

from backend.core.config import get_settings

settings = get_settings()

# Ensure the SQLite data directory exists before the engine connects.
# Without this, a fresh deployment fails with "unable to open database file"
# because the parent path (e.g. /tmp/arrnexus) does not exist yet.
if settings.database_url.startswith("sqlite"):
    db_path = settings.database_url.replace("sqlite://", "", 1)
    if db_path and not db_path.startswith(":memory:"):
        parent = os.path.dirname(os.path.abspath(db_path))
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
)
Base = declarative_base()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Initialize database tables."""
    Base.metadata.create_all(bind=engine)