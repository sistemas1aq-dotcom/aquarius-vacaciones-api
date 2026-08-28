"""Ajustes editables en caliente, guardados en la tabla AppConfig.

Por qué no basta el `.env`: `get_settings()` está cacheado con `@lru_cache`, así
que un cambio ahí solo surte efecto reiniciando uvicorn. Para un interruptor de
"deja de enviar correos" eso no vale — cuando hace falta cortar, hace falta
cortar ya, sin reiniciar el servicio.

Reparto de responsabilidades:
  - `.env` / `.env.local`  -> credenciales y datos de conexión del relay, y el
    valor INICIAL de los interruptores mientras no exista su fila.
    No son operación diaria y no deben verse en una pantalla.
  - `AppConfig`            -> lo que RRHH decide en el día a día.

Dos interruptores, con alcances distintos a propósito:

  EnvioCorreoActivo  -- permiso general. En false NO sale nada, por ninguna vía.
                        Se comprueba dentro de `SesionSmtp`, el único punto por
                        el que pasa todo envío.

  EnvioMasivoActivo  -- permiso para los envíos EN LOTE, los que alcanzan a
                        muchas personas de una vez: la corrida programada diaria
                        y el botón "Enviar a todos los pendientes". Ambos
                        ejecutan la misma función, así que se comprueba allí.
                        En false sigue siendo posible el envío por trabajador.

La combinación que interesa durante la validación es global=true, masivo=false:
correo permitido, pero solo a quien RRHH señale uno a uno.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.models import AppConfig

settings = get_settings()

CLAVE_ENVIO_CORREO = "EnvioCorreoActivo"
CLAVE_ENVIO_MASIVO = "EnvioMasivoActivo"

_VERDADEROS = {"true", "1", "si", "sí", "yes", "on", "activo"}


def obtener(db: Session, clave: str) -> Optional[str]:
    fila = db.query(AppConfig).filter(AppConfig.Clave == clave).first()
    return fila.Valor if fila else None


def guardar(db: Session, clave: str, valor: str,
            usuario: Optional[str] = None,
            descripcion: Optional[str] = None) -> None:
    fila = db.query(AppConfig).filter(AppConfig.Clave == clave).first()
    if fila is None:
        fila = AppConfig(Clave=clave, Descripcion=descripcion)
        db.add(fila)
    fila.Valor = valor
    fila.ActualizadoPor = (usuario or "")[:50] or None
    fila.ActualizadoEn = datetime.now()
    if descripcion and not fila.Descripcion:
        fila.Descripcion = descripcion
    db.commit()


def _bandera(clave: str, por_defecto: bool, db: Optional[Session] = None) -> bool:
    """Lee una bandera de AppConfig, cayendo al `.env` si no hay fila.

    Si la tabla todavía no existe (instalación que no ha corrido
    `11_app_config.sql`), se cae al valor del `.env` en vez de reventar: es
    preferible seguir funcionando como antes a dejar la aplicación rota por un
    script de base de datos pendiente.
    """
    propia = db is None
    if propia:
        from app.database import SessionLocal
        db = SessionLocal()
    try:
        valor = obtener(db, clave)
        if valor is None:
            return por_defecto
        return valor.strip().lower() in _VERDADEROS
    except Exception:  # noqa: BLE001 - tabla ausente o base no disponible
        return por_defecto
    finally:
        if propia:
            db.close()


def envio_correo_activo(db: Optional[Session] = None) -> bool:
    """¿Está permitido enviar correo ahora mismo, por la vía que sea?

    Se consulta en CADA envío, dentro de `SesionSmtp`, para que el interruptor
    no se pueda esquivar por ninguna ruta.
    """
    return _bandera(CLAVE_ENVIO_CORREO, settings.smtp_enabled, db)


def envio_masivo_activo(db: Optional[Session] = None) -> bool:
    """¿Están permitidos los envíos EN LOTE?

    Cubre las dos vías que alcanzan a muchas personas de golpe: la corrida
    programada y el botón "Enviar a todos los pendientes". No afecta al envío
    por trabajador.
    """
    return _bandera(CLAVE_ENVIO_MASIVO, settings.reminder_auto_enabled, db)


# ───────────────────────────────────────────────────────────────────
# Textos editables de los correos
#
# Se guardan en la misma AppConfig, con el prefijo `Texto`. Un párrafo por
# clave: así la pantalla de Configuraciones puede mostrar el correo por
# POSICIÓN, marcando qué bloque se toca y cuál no, y cada bloque se guarda
# por separado.
# ───────────────────────────────────────────────────────────────────

PREFIJO_TEXTO = 'Texto'


def _claves_texto() -> list[str]:
    from app.services.email_service import TEXTOS_POR_DEFECTO
    return list(TEXTOS_POR_DEFECTO.keys())


def obtener_textos(db: Session) -> dict:
    """Los bloques editables, con respaldo al valor de fábrica.

    Si la tabla no existe o falta una fila se devuelve el texto original, que
    es el que lleva el código. Una plantilla ausente no puede dejar sin correo
    a nadie.
    """
    from app.services.email_service import TEXTOS_POR_DEFECTO
    textos = dict(TEXTOS_POR_DEFECTO)
    try:
        filas = (db.query(AppConfig)
                   .filter(AppConfig.Clave.like(PREFIJO_TEXTO + '%'))
                   .all())
        for fila in filas:
            clave = fila.Clave[len(PREFIJO_TEXTO):]
            if clave in textos and (fila.Valor or '').strip():
                textos[clave] = fila.Valor
    except Exception:  # noqa: BLE001 - tabla ausente o base no disponible
        pass
    return textos


def guardar_textos(db: Session, nuevos: dict, usuario: str | None = None) -> list[str]:
    """Guarda los bloques recibidos. Devuelve los avisos para la pantalla.

    Se validan los marcadores AL GUARDAR, que es cuando hay alguien delante
    para corregirlos — no a las 08:00 del día siguiente con 25 destinatarios
    esperando.
    """
    from app.services.email_service import (
        TEXTOS_POR_DEFECTO, marcadores_desconocidos,
    )
    avisos: list[str] = []
    for clave, valor in (nuevos or {}).items():
        if clave not in TEXTOS_POR_DEFECTO:
            avisos.append(f'Se ignoró «{clave}»: no es un bloque editable.')
            continue
        valor = (valor or '').strip()
        if not valor:
            avisos.append(f'«{clave}» quedó vacío; se usará el texto original.')
        desconocidos = marcadores_desconocidos(valor)
        if desconocidos:
            avisos.append(
                f'En «{clave}» hay marcadores que no existen: '
                + ', '.join('{' + d + '}' for d in desconocidos)
                + '. Saldrán tal cual en el correo.')
        guardar(db, PREFIJO_TEXTO + clave, valor, usuario=usuario,
                descripcion='Bloque editable del correo.')
    return avisos


def estado_envio(db: Session) -> dict:
    """Estado de los dos interruptores, para pintarlos en la interfaz."""
    global_ = db.query(AppConfig).filter(AppConfig.Clave == CLAVE_ENVIO_CORREO).first()
    masivo = db.query(AppConfig).filter(AppConfig.Clave == CLAVE_ENVIO_MASIVO).first()
    return {
        "activo": envio_correo_activo(db),
        "actualizado_por": global_.ActualizadoPor if global_ else None,
        "actualizado_en": global_.ActualizadoEn if global_ else None,

        "masivo_activo": envio_masivo_activo(db),
        "masivo_actualizado_por": masivo.ActualizadoPor if masivo else None,
        "masivo_actualizado_en": masivo.ActualizadoEn if masivo else None,

        # Aunque el interruptor esté activo, sin relay configurado no sale nada.
        # Distinguir las dos cosas evita el "está en Activo pero no llega".
        "smtp_configurado": bool(settings.smtp_enabled and settings.smtp_host),
        "servidor": f"{settings.smtp_host}:{settings.smtp_port}" if settings.smtp_host else None,

        # Si el scheduler no arrancó, el interruptor de lote puede estar en
        # Activo y aun así no haber corrida diaria. Otra vez: distinguir el
        # permiso del mecanismo.
        "scheduler_activo": settings.enable_scheduler,
        "hora_corrida": f"{settings.reminder_cron_hour:02d}:{settings.reminder_cron_minute:02d}",
    }
