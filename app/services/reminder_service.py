"""Corrida de recordatorios de vacaciones pendientes.

Antes esta lógica estaba DUPLICADA: una copia en el endpoint
`POST /api/reminders/send-daily` y otra en `_daily_reminder_job` de `main.py`.
Dos copias de la misma regla de negocio acaban divergiendo; ahora ambas llaman
aquí.

Reglas de la corrida:

1. Solo entran empleados ACTIVOS cuyo saldo supera el umbral
   (`REMINDER_THRESHOLD_DAYS`) y que NO tienen vacaciones futuras aprobadas.
   Quien ya programó no necesita que le insistan.

2. **No se repite el aviso antes de `REMINDER_REPEAT_DAYS` días.** El job corre
   a diario; sin esta regla el mismo trabajador recibiría el mismo correo todas
   las mañanas, lo filtraría a los tres días y el recordatorio dejaría de servir
   justo cuando más falta hace.

3. A quien no tiene correo registrado NO se le crea un recordatorio: no hubo
   intento de envío que registrar. Se devuelven sus nombres para que RRHH
   complete el dato, que es la acción que de verdad resuelve el caso.

4. Cada recordatorio se guarda con su estado real —`sent` o `failed`— su
   `SentAt` y, si falló, el motivo. Antes todos quedaban en `pending` para
   siempre y no había forma de saber si algo había salido.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.models import VacationReminder
from app.services import config_service, email_service, vacation_service

settings = get_settings()

TIPO = "pending_30days"


def _departamento(emp) -> str:
    """Nombre del área, para los marcadores del texto editable."""
    try:
        return emp.department.Name if emp.department else ''
    except Exception:  # noqa: BLE001 - relación no cargada
        return ''


async def enviar_recordatorios_diarios(db: Session, forzar: bool = False) -> dict:
    """Ejecuta la corrida de recordatorios y devuelve su resumen.

    forzar=True ignora la regla de frecuencia (para un reenvío manual desde la
    interfaz cuando RRHH lo decide expresamente).
    """
    hoy = date.today()
    candidatos = vacation_service.get_employees_needing_reminders(
        db, settings.reminder_threshold_days
    )

    # Candado de los envios EN LOTE. Esta funcion es la UNICA via masiva:
    # la ejecutan tanto la corrida programada como el boton "Enviar a todos
    # los pendientes". Poniendo aqui la comprobacion, un solo interruptor
    # cubre las dos, y el envio por trabajador -- que no pasa por aqui --
    # sigue funcionando.
    if not config_service.envio_masivo_activo(db):
        motivo = ("Los envios EN LOTE estan DESACTIVADOS desde la aplicacion "
                  "(interruptor \u00abEnvios en lote\u00bb en Recordatorios). "
                  "El envio por trabajador sigue disponible.")
        # Si ademas el interruptor GENERAL esta apagado, hay que decirlo: si no,
        # RRHH encenderia solo el del lote y seguiria sin llegar nada.
        if not config_service.envio_correo_activo(db):
            motivo += (" Ademas el envio de correo esta DESACTIVADO en general, "
                       "asi que ahora mismo no sale nada por ninguna via.")
        return {
            "candidatos": len(candidatos),
            "enviados": 0,
            "fallidos": 0,
            "omitidos_por_frecuencia": 0,
            "sin_correo": 0,
            "detalle": {
                "fallidos": [],
                "sin_correo": [],
                "omitidos_por_frecuencia": [],
                "dias_entre_avisos": settings.reminder_repeat_days,
                "umbral_dias": settings.reminder_threshold_days,
                "error_conexion": None,
                "bloqueado_por": motivo,
            },
            "mensaje": (f"No se envio nada: {motivo} Habia {len(candidatos)} "
                        f"empleados con saldo por encima del umbral."),
        }

    omitidos_recientes: list[str] = []
    sin_correo: list[str] = []
    a_enviar = []

    for emp, balance in candidatos:
        if not forzar and _avisado_hace_poco(db, emp.Id, hoy):
            omitidos_recientes.append(emp.FullName)
            continue
        if not (emp.Email or "").strip():
            sin_correo.append(emp.FullName)
            continue
        a_enviar.append((emp, balance))

    enviados = 0
    fallidos: list[dict] = []
    error_conexion = None
    pendientes_de_aviso = len(a_enviar)

    if a_enviar:
        # Los textos editables se leen UNA vez por corrida, no por empleado:
        # que RRHH cambie una plantilla a media corrida no debe hacer que la
        # mitad del lote reciba una versión y la otra mitad otra.
        textos = config_service.obtener_textos(db)

        # Una sola conexión para todo el lote.
        async with email_service.SesionSmtp() as smtp:

            # Si lo que falla es la CONEXIÓN -- correo deshabilitado, relay
            # caído, credenciales mal -- no es un fallo "de cada empleado": es
            # uno solo, de configuración. Registrar 25 recordatorios fallidos
            # idénticos cada mañana haría crecer la tabla sin aportar nada y
            # ahogaría los fallos de verdad, como un buzón inexistente.
            # Se corta la corrida sin escribir y se informa el motivo una vez.
            if not smtp.disponible:
                error_conexion = smtp.error_conexion
                a_enviar = []

            for emp, balance in a_enviar:
                contenido = email_service.generate_reminder_email(
                    emp.FullName, balance.TotalPending,
                    balance.PendingByYear, balance.PendingTruncated,
                    department=_departamento(emp), textos=textos,
                )
                ok, error = await smtp.enviar(
                    emp.Email, contenido["subject"], contenido["body"]
                )
                vacation_service.create_reminder(
                    db, emp.Id, TIPO, emp.Email,
                    contenido["subject"], contenido["body"],
                    status="sent" if ok else "failed",
                    sent_at=datetime.now() if ok else None,
                    error=error,
                )
                if ok:
                    enviados += 1
                else:
                    fallidos.append({"empleado": emp.FullName, "motivo": error})

    return {
        "candidatos": len(candidatos),
        "enviados": enviados,
        "fallidos": len(fallidos),
        "omitidos_por_frecuencia": len(omitidos_recientes),
        "sin_correo": len(sin_correo),
        "detalle": {
            "fallidos": fallidos[:50],
            "sin_correo": sin_correo[:50],
            "omitidos_por_frecuencia": omitidos_recientes[:50],
            "dias_entre_avisos": settings.reminder_repeat_days,
            "umbral_dias": settings.reminder_threshold_days,
            "error_conexion": error_conexion,
            "bloqueado_por": None,
        },
        "mensaje": (
            f"No se envió nada: {error_conexion}. Había {pendientes_de_aviso} "
            f"empleados a los que avisar."
            if error_conexion else
            _resumen(len(candidatos), enviados, len(fallidos),
                     len(sin_correo), len(omitidos_recientes))
        ),
    }


async def enviar_a_empleado(db: Session, employee_id: int) -> dict:
    """Envía el recordatorio a UN trabajador, a petición de RRHH.

    Pensado para la fase de validación manual. A diferencia de la corrida
    automática, aquí NO se aplica la ventana de frecuencia: si alguien pulsa
    el botón es porque quiere enviarlo ahora. Sí se respeta el interruptor
    global, que corta cualquier vía.
    """
    from app.models.models import Employee, VacationBalance

    emp = db.query(Employee).filter(Employee.Id == employee_id).first()
    if emp is None:
        return {"ok": False, "error": "El empleado no existe"}
    if not (emp.Email or "").strip():
        return {"ok": False, "empleado": emp.FullName,
                "error": "El empleado no tiene correo registrado"}

    balance = (
        db.query(VacationBalance)
        .filter(VacationBalance.EmployeeId == employee_id)
        .order_by(VacationBalance.CalculationDate.desc())
        .first()
    )
    if balance is None:
        return {"ok": False, "empleado": emp.FullName,
                "error": "El empleado no tiene saldo calculado todavía"}

    contenido = email_service.generate_reminder_email(
        emp.FullName, balance.TotalPending,
        balance.PendingByYear, balance.PendingTruncated,
        department=_departamento(emp),
        textos=config_service.obtener_textos(db),
    )

    async with email_service.SesionSmtp() as smtp:
        # Misma regla que en la corrida masiva: si lo que falla es la
        # CONFIGURACION -- interruptor apagado, relay caido -- no se escribe
        # nada. No hubo intento de envio a esta persona que registrar, y llenar
        # el historial de filas identicas taparia los fallos de verdad.
        if not smtp.disponible:
            return {"ok": False, "empleado": emp.FullName, "correo": emp.Email,
                    "error": smtp.error_conexion,
                    "mensaje": f"No se envio: {smtp.error_conexion}"}

        ok, error = await smtp.enviar(
            emp.Email, contenido["subject"], contenido["body"]
        )

    vacation_service.create_reminder(
        db, emp.Id, TIPO, emp.Email,
        contenido["subject"], contenido["body"],
        status="sent" if ok else "failed",
        sent_at=datetime.now() if ok else None,
        error=error,
    )
    return {
        "ok": ok,
        "empleado": emp.FullName,
        "correo": emp.Email,
        "error": error,
        "mensaje": (f"Correo enviado a {emp.Email}" if ok
                    else f"No se pudo enviar: {error}"),
    }


def _avisado_hace_poco(db: Session, employee_id: int, hoy: date) -> bool:
    """¿Se le envió un recordatorio dentro de la ventana de frecuencia?

    Solo cuentan los que SALIERON (`sent`). Un intento fallido no consume el
    turno: si el servidor de correo estaba caído, mañana se reintenta.
    """
    desde = hoy - timedelta(days=settings.reminder_repeat_days)
    return db.query(VacationReminder.Id).filter(
        VacationReminder.EmployeeId == employee_id,
        VacationReminder.ReminderType == TIPO,
        VacationReminder.Status == "sent",
        VacationReminder.ReminderDate >= desde,
    ).first() is not None


def _resumen(candidatos: int, enviados: int, fallidos: int,
             sin_correo: int, omitidos: int) -> str:
    partes = [f"{candidatos} empleados con saldo por encima del umbral"]
    partes.append(f"{enviados} correos enviados")
    if fallidos:
        partes.append(f"{fallidos} fallidos")
    if sin_correo:
        partes.append(f"{sin_correo} sin correo registrado")
    if omitidos:
        partes.append(f"{omitidos} ya avisados recientemente")
    return ", ".join(partes) + "."
