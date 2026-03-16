# SQLAlchemy table definitions (mirrors DATA.md schema)
import os
from typing import Any
from typing import Generator

from dotenv import load_dotenv
from sqlalchemy import Boolean, Float, Integer, Text, create_engine
from sqlalchemy.orm import Mapped, Session, declarative_base, mapped_column, sessionmaker

load_dotenv()

Base = declarative_base()


class Entry(Base):
    """Represents a single CC-CEDICT dictionary entry."""

    __tablename__ = "entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    simplified: Mapped[str] = mapped_column(Text, nullable=False)
    traditional: Mapped[str] = mapped_column(Text, nullable=False)
    pinyin_toned: Mapped[str] = mapped_column(Text, nullable=False)
    pinyin_numbered: Mapped[str] = mapped_column(Text, nullable=False)
    pinyin_bare: Mapped[str] = mapped_column(Text, nullable=False)
    definitions: Mapped[str] = mapped_column(Text, nullable=False)
    is_variant: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # SUBTLEX-CH word frequency (log-transformed, higher = more frequent). Optional for v1.
    freq_log: Mapped[float | None] = mapped_column(Float, nullable=True)


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:yourpassword@localhost:5432/slipwords",
)

engine = create_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_session() -> Generator[Session, None, None]:
    """Yield a SQLAlchemy session and ensure it is closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()