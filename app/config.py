"""Configuración de la aplicación (pydantic-settings)."""
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    # Base de datos PostgreSQL
    # Puedes usar DATABASE_URL directamente (Supabase/Neon/Vercel) o las variables individuales.
    database_url: str = ""
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "aquarius_vacaciones"
    db_user: str = "postgres"
    db_password: str = "postgres"
    db_sslmode: str = "prefer"   # "require" para Supabase/Neon

    # App
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_debug: bool = False
    app_cors_origins: str = "http://localhost:4200,http://localhost"

    # Email (SMTP)
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_name: str = "RRHH Aquarius"
    smtp_from_email: str = "rrhh@aquarius.com.pe"

    # Recordatorios
    reminder_threshold_days: int = 30
    reminder_cron_hour: int = 8
    reminder_cron_minute: int = 0
    enable_scheduler: bool = True   # desactivar en Vercel/serverless

    # JWT (autenticación)
    jwt_secret_key: str = "CHANGE-ME-IN-PRODUCTION-use-a-long-random-string"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480   # 8 horas

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def effective_database_url(self) -> str:
        """Si DATABASE_URL está definido úsalo; si no, arma uno desde las variables sueltas."""
        if self.database_url:
            url = self.database_url
            # Normalizar postgres:// → postgresql:// para SQLAlchemy
            if url.startswith("postgres://"):
                url = url.replace("postgres://", "postgresql://", 1)
            # Forzar driver psycopg2 si no está especificado
            if url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
            return url
        return (
            f"postgresql+psycopg2://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
            f"?sslmode={self.db_sslmode}"
        )

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.app_cors_origins.split(",") if o.strip()]


@lru_cache()
def get_settings() -> Settings:
    return Settings()
