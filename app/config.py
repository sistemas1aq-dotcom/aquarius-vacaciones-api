"""Configuración de la aplicación (pydantic-settings)."""
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from urllib.parse import quote_plus


class Settings(BaseSettings):
    # Base de datos SQL Server
    # Puedes usar DATABASE_URL directamente o las variables individuales.
    database_url: str = ""
    db_host: str = "localhost"
    db_server: str = ""          # alias de db_host; si está puesto, manda éste
    db_port: int = 0             # 0 = omitir el puerto (el driver usa el 1433)
    db_name: str = "aquarius_vacaciones"
    db_user: str = "sa"
    db_password: str = ""
    db_driver: str = "ODBC Driver 17 for SQL Server"
    db_trusted_connection: bool = False   # True = autenticación de Windows
    db_encrypt: bool = False              # el driver 17 no cifra por defecto
    db_trust_server_certificate: bool = True

    # App
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_debug: bool = False
    app_cors_origins: str = "http://localhost:4200,http://localhost"

    # Email (SMTP)
    # smtp_enabled es un interruptor EXPLÍCITO. Antes el envío dependía de
    # que smtp_user tuviera valor, así que con la variable vacía el sistema
    # se saltaba el envío en silencio y reportaba "0 enviados" sin decir por
    # qué. Un relay interno además no suele pedir usuario.
    smtp_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 25
    smtp_user: str = ""              # vacío = relay sin autenticación
    smtp_password: str = ""
    smtp_starttls: bool = False      # True para el puerto 587
    smtp_use_tls: bool = False       # True para el puerto 465 (TLS directo)
    smtp_verify_cert: bool = True    # False si el relay usa certificado autofirmado
    smtp_timeout: int = 30
    smtp_from_name: str = "RRHH Aquarius"
    smtp_from_email: str = "rrhh@aquarius.com.pe"
    smtp_reply_to: str = ""          # a dónde llegan las respuestas

    # Recordatorios
    reminder_threshold_days: int = 30
    # Días mínimos entre dos avisos al MISMO trabajador. El job corre a
    # diario; sin esta ventana el recordatorio se convierte en spam y deja
    # de leerse.
    reminder_repeat_days: int = 15
    # ¿Se registra el job diario de recordatorios? Arranca DESACTIVADO para
    # la fase de validación manual: el envío a mano sigue disponible, pero
    # nada sale solo. Es independiente del interruptor global de correo
    # (AppConfig.EnvioCorreoActivo), que corta cualquier vía de envío.
    reminder_auto_enabled: bool = False
    reminder_cron_hour: int = 8
    reminder_cron_minute: int = 0
    enable_scheduler: bool = True   # desactivar en Vercel/serverless

    # Integración con Planillas
    # El API de exposición es el MISMO servicio que ya consume Assistime;
    # no se levanta un segundo servicio en el servidor de Planillas.
    planillas_api_url: str = ""            # p.ej. http://192.168.2.9:8090
    planillas_api_token: str = ""          # el mismo PLANILLAS_API_TOKEN del servicio
    planillas_cod_empresa: str = "0001"    # "0" = todas
    planillas_timeout_seg: int = 120
    planillas_intervalo_min: int = 15      # cada cuánto corre el sync
    planillas_sync_enabled: bool = False   # activarlo tras validar con dry_run
    planillas_max_ceses: int = 10          # freno: aborta si se cesarían más
    planillas_dias_por_anio: int = 30      # DaysPerYear de las altas

    # JWT (autenticación)
    jwt_secret_key: str = "CHANGE-ME-IN-PRODUCTION-use-a-long-random-string"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480   # 8 horas

    model_config = SettingsConfigDict(
        # .env.local se lee después de .env y sobrescribe lo que repita.
        # Está en .gitignore, así que ahí van los valores de cada máquina
        # sin tocar el .env compartido del repositorio.
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def effective_database_url(self) -> str:
        """Cadena de conexión a SQL Server vía pyodbc."""
        if self.database_url:
            return self.database_url

        # DB_SERVER tiene prioridad sobre DB_HOST: los dos nombres valen.
        # Con instancia con nombre (SERVIDOR\SQLEXPRESS) deja DB_PORT=0.
        servidor = self.db_server or self.db_host
        if self.db_port:
            servidor = f"{servidor}:{self.db_port}"

        if self.db_trusted_connection:
            credenciales = ""
        else:
            credenciales = (
                f"{quote_plus(self.db_user)}:{quote_plus(self.db_password)}@"
            )

        opciones = {
            "driver": self.db_driver,
            "Encrypt": "yes" if self.db_encrypt else "no",
            "TrustServerCertificate": "yes" if self.db_trust_server_certificate else "no",
        }
        if self.db_trusted_connection:
            opciones["Trusted_Connection"] = "yes"

        query = "&".join(f"{k}={quote_plus(v)}" for k, v in opciones.items())
        return f"mssql+pyodbc://{credenciales}{servidor}/{self.db_name}?{query}"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.app_cors_origins.split(",") if o.strip()]


@lru_cache()
def get_settings() -> Settings:
    return Settings()
