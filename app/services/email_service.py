"""Envío de correo y redacción de las notificaciones de vacaciones.

CAMBIOS RESPECTO A LA VERSIÓN ANTERIOR
--------------------------------------
1. `send_email` devolvía `True/False` y el motivo del fallo se perdía en un
   `print`. Ahora devuelve `(ok, error)` para poder guardarlo en la base.

2. Se añade `SesionSmtp`: una sola conexión reutilizada para todo el lote. Antes
   `aiosmtplib.send()` abría y cerraba una conexión por cada correo — con 50
   destinatarios eran 50 conexiones seguidas, algo que cualquier relay o Gmail
   acaba estrangulando.

3. El envío ya NO depende de que `SMTP_USER` tenga valor. Un relay interno de
   empresa normalmente no pide autenticación, y la condición anterior
   (`if emp.Email and settings.smtp_user`) hacía que el sistema se saltara el
   envío EN SILENCIO cuando esa variable estaba vacía. Ahora manda
   `SMTP_ENABLED`, que es una decisión explícita.

4. Los días se formatean: un Decimal imprimía "62.00 días" en un correo dirigido
   a una persona.

5. El recordatorio ya no lleva línea de "Total". Desde que los días pendientes
   dejaron de incluir los truncos (regla del 26/08/2026), las dos cifras del
   detalle no suman, y un total debajo hacía que el correo pareciera mal
   calculado. Se presentan como lo que son: dos derechos distintos.
"""

from __future__ import annotations

import re
import ssl
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import aiosmtplib

from app.config import get_settings

settings = get_settings()

MONTH_NAMES = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
    5: "mayo", 6: "junio", 7: "julio", 8: "agosto",
    9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
}


# ───────────────────────────────────────────────────────────────────
# Formato
# ───────────────────────────────────────────────────────────────────

def dias(valor) -> str:
    """Formatea una cantidad de días para leerla en un correo.

    62.00 -> "62"      62.50 -> "62.5"      None -> "0"
    """
    try:
        d = Decimal(str(valor if valor is not None else 0))
    except (InvalidOperation, ValueError):
        return str(valor)
    if d == d.to_integral_value():
        return str(int(d))
    return f"{d:.2f}".rstrip("0").rstrip(".")


# ───────────────────────────────────────────────────────────────────
# Transporte
# ───────────────────────────────────────────────────────────────────

def _construir_mensaje(destino: str, asunto: str, cuerpo: str) -> MIMEMultipart:
    msg = MIMEMultipart()
    msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
    msg["To"] = destino
    msg["Subject"] = asunto
    # Reply-To separado del From: permite enviar desde la cuenta que el servidor
    # autoriza y aun así recibir las respuestas en el buzón de RRHH.
    if settings.smtp_reply_to:
        msg["Reply-To"] = settings.smtp_reply_to
    msg.attach(MIMEText(cuerpo, "plain", "utf-8"))
    return msg


class SesionSmtp:
    """Conexión SMTP reutilizable para enviar un lote de correos.

    Uso:
        async with SesionSmtp() as smtp:
            ok, error = await smtp.enviar(destino, asunto, cuerpo)

    Si el envío está deshabilitado o la conexión falla, la sesión NO lanza: se
    marca como no disponible y cada `enviar` devuelve el motivo. Así una corrida
    de recordatorios registra en la base por qué no salió cada correo, en vez de
    interrumpirse a medias.
    """

    def __init__(self) -> None:
        self.cliente: Optional[aiosmtplib.SMTP] = None
        self.error_conexion: Optional[str] = None

    async def __aenter__(self) -> "SesionSmtp":
        # El interruptor global se consulta AQUÍ, en el único punto por el
        # que pasa todo envío. Ponerlo en cada endpoint dejaría la puerta
        # abierta a que una vía nueva se olvidara de comprobarlo.
        from app.services import config_service
        if not config_service.envio_correo_activo():
            self.error_conexion = (
                "Envío de correo DESACTIVADO desde la aplicación "
                "(interruptor global en Recordatorios)"
            )
            return self
        if not settings.smtp_enabled:
            self.error_conexion = (
                "Envío de correo deshabilitado (SMTP_ENABLED=false)"
            )
            return self
        if not settings.smtp_host:
            self.error_conexion = "Falta configurar SMTP_HOST"
            return self

        try:
            contexto = ssl.create_default_context()
            if not settings.smtp_verify_cert:
                # Relays internos suelen usar certificados autofirmados.
                contexto.check_hostname = False
                contexto.verify_mode = ssl.CERT_NONE

            self.cliente = aiosmtplib.SMTP(
                hostname=settings.smtp_host,
                port=settings.smtp_port,
                use_tls=settings.smtp_use_tls,
                start_tls=settings.smtp_starttls,
                tls_context=contexto,
                timeout=settings.smtp_timeout,
            )
            await self.cliente.connect()
            # Un relay interno normalmente no pide credenciales.
            if settings.smtp_user:
                await self.cliente.login(settings.smtp_user, settings.smtp_password)
        except Exception as exc:  # noqa: BLE001
            self.cliente = None
            self.error_conexion = f"No se pudo conectar al servidor de correo: {exc}"
        return self

    async def __aexit__(self, *_) -> None:
        if self.cliente is not None:
            try:
                await self.cliente.quit()
            except Exception:  # noqa: BLE001 - cerrar nunca debe romper la corrida
                pass
            self.cliente = None

    @property
    def disponible(self) -> bool:
        return self.cliente is not None

    async def enviar(self, destino: str, asunto: str, cuerpo: str) -> tuple[bool, Optional[str]]:
        """Envía un correo. Devuelve (ok, motivo_del_fallo)."""
        if not destino:
            return False, "El empleado no tiene correo registrado"
        if self.cliente is None:
            return False, self.error_conexion or "Sesión SMTP no disponible"
        try:
            await self.cliente.send_message(_construir_mensaje(destino, asunto, cuerpo))
            return True, None
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"


async def send_email(destino: str, asunto: str, cuerpo: str) -> tuple[bool, Optional[str]]:
    """Envía UN correo suelto abriendo y cerrando su propia conexión.

    Para lotes usa `SesionSmtp`, que reaprovecha la conexión.
    """
    async with SesionSmtp() as smtp:
        return await smtp.enviar(destino, asunto, cuerpo)


async def probar_conexion() -> tuple[bool, Optional[str]]:
    """Comprueba que el servidor de correo responde, sin enviar nada.

    Pensado para un endpoint de diagnóstico: distingue "mal configurado" de
    "no hay a quién enviar", que es justo lo que antes no se podía distinguir.
    """
    async with SesionSmtp() as smtp:
        if smtp.disponible:
            return True, None
        return False, smtp.error_conexion


# ───────────────────────────────────────────────────────────────────
# Redacción
# ───────────────────────────────────────────────────────────────────

FIRMA = "Atentamente,\nRecursos Humanos - AQUARIUS"


# ───────────────────────────────────────────────────────────────────
# Bloques de texto que RRHH puede editar desde Configuraciones
#
# El correo se arma por POSICIÓN: unos párrafos son editables y otros no.
# Los fijos son los que llevan los datos calculados — el saludo con el
# nombre y el bloque de cifras — porque ahí un dedazo no cambia la
# redacción, cambia lo que el trabajador entiende que se le debe.
#
# Estos valores son el punto de partida y el respaldo: si no hay fila en
# AppConfig (por ejemplo, porque no se ha corrido 12_textos_correo.sql),
# el correo sale exactamente igual que antes.
# ───────────────────────────────────────────────────────────────────

TXT_CONVOCATORIA_ASUNTO = 'ConvocatoriaAsunto'
TXT_CONVOCATORIA_INTRO  = 'ConvocatoriaIntro'
TXT_CONVOCATORIA_CIERRE = 'ConvocatoriaCierre'
TXT_RECORDATORIO_INTRO  = 'RecordatorioIntro'
TXT_RECORDATORIO_CIERRE = 'RecordatorioCierre'

TEXTOS_POR_DEFECTO: dict[str, str] = {
    TXT_CONVOCATORIA_ASUNTO:
        'Convocatoria a Reunión - Recursos Humanos',
    TXT_CONVOCATORIA_INTRO:
        'Le convocamos a una reunión con el área de Recursos Humanos para '
        'conversar sobre la planificación de sus vacaciones pendientes.',
    TXT_CONVOCATORIA_CIERRE:
        'Le agradecemos confirmar su disponibilidad respondiendo a este correo.'
        '\n\nAtentamente,\nRecursos Humanos - AQUARIUS',
    TXT_RECORDATORIO_INTRO:
        'Le recordamos que cuenta con {dias_pendientes} días de vacaciones '
        'pendientes de goce.',
    TXT_RECORDATORIO_CIERRE:
        'De acuerdo con la normativa laboral vigente, es necesario que programe '
        'sus vacaciones a la brevedad posible.'
        '\n\nPor favor, coordine con su jefe directo y el área de Recursos '
        'Humanos para establecer las fechas de su descanso vacacional.'
        '\n\nAtentamente,\nRecursos Humanos - AQUARIUS',
}

#: Los únicos marcadores que se sustituyen en los párrafos editables.
MARCADORES = ('nombre', 'departamento', 'dias_pendientes', 'dias_truncos')

_RE_MARCADOR = re.compile(r'\{([a-zA-Z_]+)\}')


def aplicar_marcadores(texto: str, valores: dict) -> str:
    """Sustituye {marcador} por su valor. NUNCA lanza.

    Deliberadamente no se usa `str.format()`: una llave suelta o un marcador
    mal escrito lo harían reventar, y reventaría a las 08:00 en mitad de una
    corrida, con la mitad de los destinatarios avisados. Aquí un marcador
    desconocido se queda tal cual en el texto: se ve, se corrige, y mientras
    tanto el correo sale.
    """
    def _sustituir(m):
        clave = m.group(1)
        if clave in valores:
            return str(valores[clave])
        return m.group(0)
    return _RE_MARCADOR.sub(_sustituir, texto or '')


def marcadores_desconocidos(texto: str) -> list[str]:
    """Marcadores del texto que no existen. Para avisar AL GUARDAR."""
    return sorted({m for m in _RE_MARCADOR.findall(texto or '')
                   if m not in MARCADORES})


def _txt(textos, clave: str) -> str:
    """Texto editable, con respaldo al valor de fábrica."""
    if textos and textos.get(clave):
        return textos[clave]
    return TEXTOS_POR_DEFECTO[clave]


def generate_extension_email(
    emp_name: str, start_date: date, original_end: date,
    new_end: date, extra_days: int, total_days: Decimal
) -> dict:
    """Correo de extensión de vacaciones."""
    regreso = new_end + timedelta(days=1)
    return {
        "subject": f"Extensión de Vacaciones - {emp_name}",
        "body": (
            f"Estimado(a) {emp_name},\n\n"
            f"Por medio de la presente le comunicamos que su período de vacaciones ha sido extendido.\n\n"
            f"Detalles actualizados:\n"
            f"  • Fecha de inicio: {start_date.strftime('%d/%m/%Y')}\n"
            f"  • Fecha de fin original: {original_end.strftime('%d/%m/%Y')}\n"
            f"  • Nueva fecha de fin: {new_end.strftime('%d/%m/%Y')}\n"
            f"  • Días adicionales: {dias(extra_days)}\n"
            f"  • Total de días: {dias(total_days)}\n\n"
            f"Le recordamos que deberá reincorporarse a sus labores el día "
            f"{regreso.strftime('%d/%m/%Y')}.\n\n"
            f"{FIRMA}"
        ),
    }


def estructura_correos(textos: dict | None = None,
                       nombre: str = 'GOMEZ LOPEZ ANA',
                       departamento: str = 'SISTEMAS',
                       pendientes: Decimal = Decimal('62'),
                       truncos: Decimal = Decimal('12.5')) -> dict:
    """Los dos correos descompuestos EN BLOQUES, en orden de aparición.

    Cada bloque dice si se puede editar o no. La pantalla de Configuraciones
    se limita a pintar esta lista: así lo que se ve editable es exactamente
    lo que el backend deja editar, y no hay dos versiones de esa verdad — una
    en Angular y otra aquí — que puedan separarse con el tiempo.

    Los bloques fijos se devuelven ya rellenos con datos de muestra, para que
    se lean como el correo de verdad.
    """
    def fijo(etiqueta, texto, motivo):
        return {'tipo': 'fijo', 'etiqueta': etiqueta,
                'texto': texto, 'motivo': motivo}

    def editable(etiqueta, clave, ayuda):
        return {'tipo': 'editable', 'etiqueta': etiqueta, 'clave': clave,
                'texto': _txt(textos, clave), 'ayuda': ayuda}

    d_pend, d_trunc = dias(pendientes), dias(truncos)

    return {
        'recordatorio': {
            'titulo': 'Recordatorio de vacaciones pendientes',
            'descripcion': 'Se envía a quien supera el umbral de días sin programar.',
            'asunto': fijo(
                'Asunto',
                f'Recordatorio: tiene {d_pend} días de vacaciones pendientes',
                'Lleva el número de días calculado.'),
            'bloques': [
                fijo('Saludo', f'Estimado(a) {nombre},',
                     'Lleva el nombre del trabajador.'),
                editable('Párrafo de apertura', TXT_RECORDATORIO_INTRO,
                         'Primera frase del correo.'),
                fijo('Detalle de días',
                     'Detalle:\n'
                     f'  • Vacaciones por año cumplido pendientes: {d_pend} días\n'
                     f'  • Vacaciones truncas (período actual): {d_trunc} días '
                     '(derecho en formación; no se programan todavía)',
                     'Son las cifras del saldo: no se editan para que no puedan '
                     'contradecir lo que dice el sistema.'),
                editable('Cierre y firma', TXT_RECORDATORIO_CIERRE,
                         'Todo lo que va después de las cifras, firma incluida.'),
            ],
        },
        'convocatoria': {
            'titulo': 'Convocatoria a reunión con RRHH',
            'descripcion': 'Se envía desde el dashboard, uno a uno, a los casos críticos.',
            'asunto': editable('Asunto', TXT_CONVOCATORIA_ASUNTO,
                              'Asunto del correo de convocatoria.'),
            'bloques': [
                fijo('Saludo', f'Estimado(a) {nombre},',
                     'Lleva el nombre del trabajador.'),
                editable('Párrafo de apertura', TXT_CONVOCATORIA_INTRO,
                         'Primera frase del correo.'),
                fijo('Datos del colaborador',
                     'Datos del colaborador:\n'
                     f'  • Departamento: {departamento}\n'
                     f'  • Días pendientes de programar: {d_pend}\n'
                     f'  • Vacaciones truncas del período en curso: {d_trunc}',
                     'Son las cifras del saldo: no se editan para que no puedan '
                     'contradecir lo que dice el sistema.'),
                editable('Cierre y firma', TXT_CONVOCATORIA_CIERRE,
                         'Todo lo que va después de los datos, firma incluida.'),
            ],
        },
    }


def generate_hr_meeting_email(
    emp_name: str, department: str,
    pending_year: Decimal, pending_truncated: Decimal,
    textos: dict | None = None,
) -> dict:
    """Convocatoria a reunión con Recursos Humanos.

    Estructura por posición — entre corchetes, lo que RRHH puede editar:

        Estimado(a) NOMBRE,          <- fijo
        [intro]                      <- editable
        Datos del colaborador: ...   <- fijo (las cifras)
        [cierre y firma]             <- editable
    """
    valores = {
        'nombre': emp_name,
        'departamento': department or '—',
        'dias_pendientes': dias(pending_year),
        'dias_truncos': dias(pending_truncated),
    }
    intro  = aplicar_marcadores(_txt(textos, TXT_CONVOCATORIA_INTRO), valores)
    cierre = aplicar_marcadores(_txt(textos, TXT_CONVOCATORIA_CIERRE), valores)
    asunto = aplicar_marcadores(_txt(textos, TXT_CONVOCATORIA_ASUNTO), valores)

    return {
        "subject": asunto,
        "body": (
            f"Estimado(a) {emp_name},\n\n"
            f"{intro}\n\n"
            f"Datos del colaborador:\n"
            f"  • Departamento: {valores['departamento']}\n"
            f"  • Días pendientes de programar: {dias(pending_year)}\n"
            f"  • Vacaciones truncas del período en curso: {dias(pending_truncated)}\n\n"
            f"{cierre}"
        ),
    }


def generate_reminder_email(
    emp_name: str, total_pending: Decimal,
    pending_year: Decimal, pending_truncated: Decimal,
    department: str = '', textos: dict | None = None,
) -> dict:
    """Recordatorio de vacaciones pendientes.

    Estructura por posición — entre corchetes, lo que RRHH puede editar:

        Asunto                       <- fijo (lleva el número de días)
        Estimado(a) NOMBRE,          <- fijo
        [intro]                      <- editable
        Detalle: ...                 <- fijo (las cifras)
        [cierre y firma]             <- editable
    """
    valores = {
        'nombre': emp_name,
        'departamento': department or '—',
        'dias_pendientes': dias(pending_year),
        'dias_truncos': dias(pending_truncated),
    }
    intro  = aplicar_marcadores(_txt(textos, TXT_RECORDATORIO_INTRO), valores)
    cierre = aplicar_marcadores(_txt(textos, TXT_RECORDATORIO_CIERRE), valores)

    return {
        "subject": f"Recordatorio: tiene {dias(total_pending)} días de vacaciones pendientes",
        "body": (
            f"Estimado(a) {emp_name},\n\n"
            f"{intro}\n\n"
            f"Detalle:\n"
            f"  • Vacaciones por año cumplido pendientes: {dias(pending_year)} días\n"
            f"  • Vacaciones truncas (período actual): {dias(pending_truncated)} días "
            f"(derecho en formación; no se programan todavía)\n\n"
            f"{cierre}"
        ),
    }
