"""Conexión a PostgreSQL y gestión de sesiones."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.effective_database_url,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    echo=settings.app_debug,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """Dependencia: yields una sesión y asegura el cierre."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
