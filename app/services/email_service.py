"""Email service for sending vacation notifications."""
import aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import date
from decimal import Decimal
from app.config import get_settings

settings = get_settings()

MONTH_NAMES = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
    5: "mayo", 6: "junio", 7: "julio", 8: "agosto",
    9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
}


async def send_email(to: str, subject: str, body: str) -> bool:
    """Send an email via SMTP."""
    try:
        msg = MIMEMultipart()
        msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user,
            password=settings.smtp_password,
            use_tls=False,
            start_tls=True,
        )
        return True
    except Exception as e:
        print(f"Email send error: {e}")
        return False


def generate_extension_email(
    emp_name: str, start_date: date, original_end: date,
    new_end: date, extra_days: int, total_days: Decimal
) -> dict:
    """Generate email body for vacation extension."""
    return_date = new_end + __import__("datetime").timedelta(days=1)
    return {
        "subject": f"Extensión de Vacaciones - {emp_name}",
        "body": (
            f"Estimado(a) {emp_name},\n\n"
            f"Por medio de la presente le comunicamos que su período de vacaciones ha sido extendido.\n\n"
            f"Detalles actualizados:\n"
            f"  • Fecha de inicio: {start_date.strftime('%d/%m/%Y')}\n"
            f"  • Fecha de fin original: {original_end.strftime('%d/%m/%Y')}\n"
            f"  • Nueva fecha de fin: {new_end.strftime('%d/%m/%Y')}\n"
            f"  • Días adicionales: {extra_days}\n"
            f"  • Total de días: {total_days}\n\n"
            f"Le recordamos que deberá reincorporarse a sus labores el día "
            f"{return_date.strftime('%d/%m/%Y')}.\n\n"
            f"Atentamente,\n"
            f"Recursos Humanos - AQUARIUS"
        ),
    }


def generate_hr_meeting_email(
    emp_name: str, position: str, department: str,
    hire_date: date, total_pending: Decimal,
    pending_year: Decimal, pending_truncated: Decimal
) -> dict:
    """Generate email for HR meeting convocation."""
    return {
        "subject": f"Convocatoria a Reunión - Recursos Humanos",
        "body": (
            f"Estimado(a) {emp_name},\n\n"
            f"Le convocamos a una reunión con el área de Recursos Humanos para conversar "
            f"sobre su nueva asignación y planificación de vacaciones pendientes.\n\n"
            f"Datos del colaborador:\n"
            f"  • Cargo: {position}\n"
            f"  • Departamento: {department}\n"
            f"  • Fecha de ingreso: {hire_date.strftime('%d/%m/%Y')}\n"
            f"  • Días de vacaciones pendientes: {total_pending}\n"
            f"  • Pendientes por año cumplido: {pending_year}\n"
            f"  • Vacaciones truncas: {pending_truncated}\n\n"
            f"Le agradeceremos confirmar su disponibilidad respondiendo a este correo.\n\n"
            f"Atentamente,\n"
            f"Recursos Humanos - AQUARIUS"
        ),
    }


def generate_reminder_email(
    emp_name: str, total_pending: Decimal,
    pending_year: Decimal, pending_truncated: Decimal
) -> dict:
    """Generate daily reminder email for employees with 30+ pending days."""
    return {
        "subject": f"RECORDATORIO: Vacaciones Pendientes - {emp_name}",
        "body": (
            f"Estimado(a) {emp_name},\n\n"
            f"Le recordamos que cuenta con {total_pending} días de vacaciones pendientes de goce.\n\n"
            f"Detalle:\n"
            f"  • Vacaciones por año cumplido pendientes: {pending_year} días\n"
            f"  • Vacaciones truncas (período actual): {pending_truncated} días\n"
            f"  • Total: {total_pending} días\n\n"
            f"De acuerdo con la normativa laboral vigente, es necesario que programe "
            f"sus vacaciones a la brevedad posible.\n\n"
            f"Por favor, coordine con su jefe directo y el área de Recursos Humanos "
            f"para establecer las fechas de su descanso vacacional.\n\n"
            f"Recursos Humanos - AQUARIUS"
        ),
    }
