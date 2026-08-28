"""API Routes for the Vacation Management System."""
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session
from datetime import date, datetime
from typing import Optional
from decimal import Decimal

from app.database import get_db
from app.schemas.schemas import (
    EmployeeCreate, EmployeeUpdate, EmployeeWithBalance,
    VacationCreate, VacationUpdate, VacationExtend, VacationResponse,
    EmailDraft, ReminderResponse, MessageResponse,
    DepartmentResponse,
)
from app.services import (
    employee_service, vacation_service, email_service, reminder_service,
    config_service,
    report_service,
)
from app.models.models import User
from app.auth.dependencies import require_auth, require_admin
from app.config import get_settings

settings = get_settings()

# ─── Routers (todos requieren autenticación) ─────────────────────
_auth_dep = [Depends(require_auth)]
employees_router   = APIRouter(prefix="/api/employees",   tags=["Employees"],   dependencies=_auth_dep)
vacations_router   = APIRouter(prefix="/api/vacations",   tags=["Vacations"],   dependencies=_auth_dep)
dashboard_router   = APIRouter(prefix="/api/dashboard",   tags=["Dashboard"],   dependencies=_auth_dep)
reports_router     = APIRouter(prefix="/api/reports",     tags=["Reports"],     dependencies=_auth_dep)
reminders_router   = APIRouter(prefix="/api/reminders",   tags=["Reminders"],   dependencies=_auth_dep)
departments_router = APIRouter(prefix="/api/departments", tags=["Departments"], dependencies=_auth_dep)


# ═══════════════════════════════════════════════════════════════════
# DEPARTMENTS
# ═══════════════════════════════════════════════════════════════════
@departments_router.get("", response_model=list[DepartmentResponse])
def list_departments(db: Session = Depends(get_db)):
    return employee_service.get_departments(db)


# ═══════════════════════════════════════════════════════════════════
# EMPLOYEES
# ═══════════════════════════════════════════════════════════════════
@employees_router.get("")
def list_employees(
    department: Optional[str] = None,
    search: Optional[str] = None,
    includeInactive: bool = Query(False, description="Si es True devuelve también empleados inactivos"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
):
    # is_active=None => todos | True => solo activos
    is_active_filter = None if includeInactive else True
    items, total = employee_service.get_employees(
        db, department=department, search=search,
        is_active=is_active_filter, page=page, page_size=pageSize
    )
    return {
        "items": items,
        "total": total,
        "page": page,
        "pageSize": pageSize,
        "totalPages": (total + pageSize - 1) // pageSize,
    }


@employees_router.get("/{employee_id}")
def get_employee(employee_id: int, db: Session = Depends(get_db)):
    result = employee_service.get_employee_detail(db, employee_id)
    if not result:
        raise HTTPException(status_code=404, detail="Empleado no encontrado")
    return result


@employees_router.post("", status_code=201)
def create_employee(data: EmployeeCreate, db: Session = Depends(get_db)):
    emp = employee_service.create_employee(db, data)
    return {"message": "Empleado creado exitosamente", "id": emp.Id}


@employees_router.put("/{employee_id}")
def update_employee(employee_id: int, data: EmployeeUpdate, db: Session = Depends(get_db)):
    emp = employee_service.update_employee(db, employee_id, data)
    if not emp:
        raise HTTPException(status_code=404, detail="Empleado no encontrado")
    return {"message": "Empleado actualizado exitosamente"}


# ═══════════════════════════════════════════════════════════════════
# VACATIONS
# ═══════════════════════════════════════════════════════════════════
@vacations_router.get("/employee/{employee_id}", response_model=list[VacationResponse])
def list_employee_vacations(employee_id: int, db: Session = Depends(get_db)):
    return vacation_service.get_vacations_by_employee(db, employee_id)


@vacations_router.post("", status_code=201)
def create_vacation(data: VacationCreate, db: Session = Depends(get_db)):
    vac = vacation_service.create_vacation(db, data)
    return {"message": "Vacación registrada exitosamente", "id": vac.Id}


@vacations_router.put("/{vacation_id}")
def update_vacation(vacation_id: int, data: VacationUpdate, db: Session = Depends(get_db)):
    vac = vacation_service.update_vacation(db, vacation_id, data)
    if not vac:
        raise HTTPException(status_code=404, detail="Registro de vacación no encontrado")
    return {"message": "Vacación actualizada exitosamente"}


@vacations_router.delete("/{vacation_id}")
def delete_vacation(vacation_id: int, db: Session = Depends(get_db)):
    if not vacation_service.delete_vacation(db, vacation_id):
        raise HTTPException(status_code=404, detail="Registro de vacación no encontrado")
    return {"message": "Vacación eliminada exitosamente"}


@vacations_router.post("/{vacation_id}/extend")
def extend_vacation(vacation_id: int, data: VacationExtend, db: Session = Depends(get_db)):
    vac = vacation_service.extend_vacation(db, vacation_id, data.ExtraDays, data.Notes)
    if not vac:
        raise HTTPException(status_code=404, detail="Registro de vacación no encontrado")
    return {"message": f"Vacación extendida +{data.ExtraDays} días exitosamente"}


# ═══════════════════════════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════════════════════════
@dashboard_router.get("")
def get_dashboard(db: Session = Depends(get_db)):
    data = employee_service.get_dashboard_data(db)

    # Los empleados se traen UNA vez y se indexan. Antes `_balance_to_alert`
    # lanzaba una consulta por cada fila de cada lista: con criticos, +30 y
    # adelantos eran decenas de viajes a la base por cada carga del tablero.
    from app.models.models import Employee as _Emp, Department as _Dep
    _empleados = {
        e.Id: e for e in db.query(_Emp).outerjoin(_Dep, _Emp.DepartmentId == _Dep.Id).all()
    }

    def _depto(emp):
        return emp.department.Name if emp and emp.department else ""

    def _balance_to_alert(b, alert_type):
        emp = _empleados.get(b.EmployeeId)
        return {
            "AlertType": alert_type,
            "EmployeeId": b.EmployeeId,
            "EmployeeName": emp.FullName if emp else "",
            "Department": _depto(emp),
            "Position": (emp.Position or "") if emp else "",
            "Email": emp.Email if emp else "",
            "TotalPending": float(b.TotalPending),
            "PendingByYear": float(b.PendingByYear),
            "PendingTruncated": float(b.PendingTruncated),
        }

    def _vac_to_alert(v, alert_type):
        emp = _empleados.get(v.EmployeeId)
        return {
            "AlertType": alert_type,
            "EmployeeId": v.EmployeeId,
            "EmployeeName": emp.FullName if emp else "",
            "Department": _depto(emp),
            "VacationId": v.Id,
            "StartDate": v.StartDate.isoformat() if v.StartDate else None,
            "EndDate": v.EndDate.isoformat() if v.EndDate else None,
            "Days": float(v.Days),
        }

    def _emp_to_alert(emp, alert_type):
        """Empleado sin saldo asociado -- p. ej. el que no tiene correo."""
        return {
            "AlertType": alert_type,
            "EmployeeId": emp.Id,
            "EmployeeName": emp.FullName,
            "Department": _depto(emp),
            "Position": emp.Position or "",
            "Email": emp.Email or "",
        }

    def _empleado_con_saldo(emp, b):
        """Fila del padron: el empleado, con su saldo si lo tiene."""
        return {
            "AlertType": "employee",
            "EmployeeId": emp.Id,
            "EmployeeName": emp.FullName,
            "Department": _depto(emp),
            "Position": emp.Position or "",
            "Email": emp.Email or "",
            "TotalPending": float(b.TotalPending) if b else 0.0,
            "PendingByYear": float(b.PendingByYear) if b else 0.0,
            "PendingTruncated": float(b.PendingTruncated) if b else 0.0,
        }

    def _recordatorio_to_alert(r):
        """Correo fallido. Lleva el motivo: sin el, la lista no sirve."""
        emp = _empleados.get(r.EmployeeId)
        return {
            "AlertType": "failed_email",
            "EmployeeId": r.EmployeeId,
            "EmployeeName": emp.FullName if emp else "",
            "Department": _depto(emp),
            "Email": r.EmailTo or "",
            "StartDate": r.ReminderDate.isoformat() if r.ReminderDate else None,
            "ErrorMessage": r.ErrorMessage,
        }

    return {
        "Stats": data["stats"],
        "Critical": [_balance_to_alert(b, "critical") for b in data["critical"]],
        "Pending30": [_balance_to_alert(b, "pending_30") for b in data["pending_30"]],
        "NextWeekOut": [_vac_to_alert(v, "next_week_out") for v in data["next_out"]],
        "NextWeekReturn": [_vac_to_alert(v, "next_week_return") for v in data["next_return"]],
        "InProgress": [_vac_to_alert(v, "in_progress") for v in data["in_progress"]],
        "Advanced": [_balance_to_alert(b, "advanced") for b in data["advanced"]],
        "NoProgrammed": [_balance_to_alert(b, "no_programmed") for b in data["sin_programar"]],
        "Truncated": [_balance_to_alert(b, "truncated") for b in data["truncos"]],
        "AllPending": [_balance_to_alert(b, "pending") for b in data["pendientes"]],
        # El padron completo. Se construye desde Employees y NO desde los
        # saldos, para que el largo de la lista coincida siempre con el numero
        # de la tarjeta: si alguien se quedara sin fila de saldo, seguiria
        # apareciendo, que es justo cuando hace falta verlo.
        "Employees": [
            _empleado_con_saldo(e, data["balances"].get(e.Id))
            for e in sorted((x for x in _empleados.values() if x.IsActive),
                            key=lambda x: x.FullName or "")
        ],
        "NoEmail": [_emp_to_alert(e, "no_email") for e in data["sin_correo"]],
        "FailedEmails": [_recordatorio_to_alert(r) for r in data["correos_fallidos"]],
        "Peak": {
            "Fecha": data["pico"]["fecha"],
            "Total": data["pico"]["total"],
            "Desde": data["pico"]["desde"],
            "Hasta": data["pico"]["hasta"],
            "Items": [_vac_to_alert(v, "peak") for v in data["pico"]["vacaciones"]],
        },
    }


# ═══════════════════════════════════════════════════════════════════
# REPORTS
# ═══════════════════════════════════════════════════════════════════
@reports_router.get("/monthly")
def monthly_report(year: int = Query(2026), db: Session = Depends(get_db)):
    return employee_service.get_monthly_report(db, year)


@reports_router.get("/departments")
def department_report(db: Session = Depends(get_db)):
    return employee_service.get_department_report(db)


@reports_router.get("/projection")
def projection_report(year: int = Query(2026), db: Session = Depends(get_db)):
    return employee_service.get_projection(db, year)


# ── Reporte Corporativo de Vacaciones ──────────────────────────────
# Las tres salidas se arman desde la MISMA función. Si cada una calculara
# lo suyo, el Excel y la pantalla acabarían diciendo cosas distintas, y el
# que se manda a gerencia es el Excel.

@reports_router.get("/vacation-report/preview")
def vacation_report_preview(
    year: int = Query(default_factory=lambda: date.today().year),
    db: Session = Depends(get_db),
):
    """Filas y totales del reporte, para pintarlo en pantalla."""
    return report_service.datos_reporte(db, year)


@reports_router.get("/vacation-report/xlsx")
def vacation_report_xlsx(
    year: int = Query(default_factory=lambda: date.today().year),
    db: Session = Depends(get_db),
):
    """El reporte en Excel, con los colores del formato corporativo."""
    datos = report_service.datos_reporte(db, year)
    try:
        contenido = report_service.generar_xlsx(datos)
    except ImportError:
        # Mejor decir qué falta que devolver un 500 opaco: la dependencia se
        # instala en un minuto, pero solo si alguien sabe cuál es.
        raise HTTPException(
            status_code=503,
            detail="Falta la librería openpyxl en el servidor. Instálala con: pip install openpyxl")
    return Response(
        content=contenido,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition":
                 f'attachment; filename="reporte_vacaciones_{year}.xlsx"'},
    )


@reports_router.get("/vacation-report/pdf")
def vacation_report_pdf(
    year: int = Query(default_factory=lambda: date.today().year),
    db: Session = Depends(get_db),
):
    """El reporte en PDF, apaisado: doce columnas no caben en vertical."""
    datos = report_service.datos_reporte(db, year)
    try:
        contenido = report_service.generar_pdf(datos)
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="Falta la librería reportlab en el servidor. Instálala con: pip install reportlab")
    return Response(
        content=contenido,
        media_type="application/pdf",
        headers={"Content-Disposition":
                 f'attachment; filename="reporte_vacaciones_{year}.pdf"'},
    )


@reports_router.get("/top-pending")
def top_pending_report(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    items, _ = employee_service.get_employees(db, page=1, page_size=500)
    sorted_items = sorted(items, key=lambda x: float(x.get("TotalPending", 0)), reverse=True)
    return sorted_items[:limit]


# ═══════════════════════════════════════════════════════════════════
# REMINDERS
# ═══════════════════════════════════════════════════════════════════
@reminders_router.get("")
def list_reminders(
    page: int = Query(1, ge=1),
    pageSize: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    items, total = vacation_service.get_reminders(db, page, pageSize)
    return {
        "items": items,
        "total": total,
        "page": page,
        "pageSize": pageSize,
    }


# La restricción CHECK de VacationReminders solo admite estos cuatro valores;
# cualquier otro haría fallar el INSERT.
_TIPOS_RECORDATORIO = {"pending_30days", "hr_meeting", "extension", "custom"}


def _tipo_valido(tipo: Optional[str]) -> str:
    return tipo if tipo in _TIPOS_RECORDATORIO else "custom"


@reminders_router.post("/send-daily")
async def send_daily_reminders(
    forzar: bool = Query(False, description="Ignora la ventana entre avisos y reenvía igual"),
    db: Session = Depends(get_db),
):
    """Corrida de recordatorios a quienes superan el umbral y no tienen vacaciones programadas.

    La lógica vive en `reminder_service` porque el job de las 08:00 ejecuta
    exactamente lo mismo; tenerla duplicada hacía que las dos copias
    pudieran divergir.
    """
    return await reminder_service.enviar_recordatorios_diarios(db, forzar=forzar)


@reminders_router.get("/envio")
def estado_envio(db: Session = Depends(get_db)):
    """Estado del interruptor global de correo."""
    return config_service.estado_envio(db)


@reminders_router.put("/envio")
def cambiar_envio(
    activo: bool = Query(..., description="true = se envían correos; false = se cancela todo envío"),
    db: Session = Depends(get_db),
    usuario: User = Depends(require_admin),
):
    """Activa o desactiva TODO el envío de correo, al instante.

    Aplica a cualquier vía: corrida automática, envío individual y correos
    puntuales. Se comprueba dentro de `SesionSmtp`, así que ninguna ruta
    puede saltárselo. Solo un administrador puede cambiarlo.
    """
    config_service.guardar(
        db, config_service.CLAVE_ENVIO_CORREO,
        "true" if activo else "false",
        usuario=usuario.Username,
        descripcion="Interruptor global: si es false NO sale ningún correo.",
    )
    return config_service.estado_envio(db)


@reminders_router.put("/envio-masivo")
def cambiar_envio_masivo(
    activo: bool = Query(..., description="true = se permiten los envios en lote"),
    db: Session = Depends(get_db),
    usuario: User = Depends(require_admin),
):
    """Activa o desactiva los envios EN LOTE, al instante.

    Cubre las dos vias que alcanzan a mucha gente de una vez: la corrida
    programada diaria y el boton "Enviar a todos los pendientes". No afecta
    al envio por trabajador, que sigue disponible mientras el interruptor
    general este activo. Solo un administrador puede cambiarlo.
    """
    config_service.guardar(
        db, config_service.CLAVE_ENVIO_MASIVO,
        "true" if activo else "false",
        usuario=usuario.Username,
        descripcion="Permite los envios en lote: corrida programada y boton masivo.",
    )
    return config_service.estado_envio(db)


# ── Textos editables de los correos ────────────────────────────────

#: Empleado de muestra para la vista previa. Sin tocar la base: la vista
#: previa tiene que funcionar aunque no haya nadie que cumpla el umbral.
_MUESTRA = {
    'nombre': 'GOMEZ LOPEZ ANA',
    'departamento': 'SISTEMAS',
    'pendientes': Decimal('62'),
    'truncos': Decimal('12.5'),
}


def _vista_previa(textos: dict) -> dict:
    """Los dos correos ya montados, para verlos antes de guardar."""
    return {
        'recordatorio': email_service.generate_reminder_email(
            _MUESTRA['nombre'], _MUESTRA['pendientes'],
            _MUESTRA['pendientes'], _MUESTRA['truncos'],
            department=_MUESTRA['departamento'], textos=textos),
        'convocatoria': email_service.generate_hr_meeting_email(
            _MUESTRA['nombre'], _MUESTRA['departamento'],
            _MUESTRA['pendientes'], _MUESTRA['truncos'], textos=textos),
    }


@reminders_router.get("/textos")
def obtener_textos(db: Session = Depends(get_db)):
    """Bloques editables de los correos, con su vista previa."""
    textos = config_service.obtener_textos(db)
    return {
        "textos": textos,
        "marcadores": list(email_service.MARCADORES),
        "por_defecto": email_service.TEXTOS_POR_DEFECTO,
        "estructura": email_service.estructura_correos(textos),
        "vista_previa": _vista_previa(textos),
    }


@reminders_router.post("/textos/vista-previa")
def vista_previa_textos(borrador: dict, db: Session = Depends(get_db)):
    """Monta los correos con un borrador SIN GUARDAR.

    Existe para que la vista previa se actualice mientras se escribe, sin
    obligar a grabar para ver el resultado. Lo importante es que la arma el
    MISMO código que envía el correo: si la compusiera el frontend habría dos
    montajes de la misma carta y acabarían diciendo cosas distintas.
    """
    textos = config_service.obtener_textos(db)
    avisos: list[str] = []
    for clave, valor in (borrador or {}).items():
        if clave not in email_service.TEXTOS_POR_DEFECTO:
            continue
        valor = (valor or '').strip()
        if valor:
            textos[clave] = valor
        desconocidos = email_service.marcadores_desconocidos(valor)
        if desconocidos:
            avisos.append(
                f'En «{clave}» hay marcadores que no existen: '
                + ', '.join('{' + d + '}' for d in desconocidos)
                + '. Saldrán tal cual en el correo.')
    return {
        "vista_previa": _vista_previa(textos),
        "avisos": avisos,
    }


@reminders_router.put("/textos")
def guardar_textos(
    nuevos: dict,
    db: Session = Depends(get_db),
    usuario: User = Depends(require_admin),
):
    """Guarda los bloques editables. Solo un administrador.

    Los avisos (marcadores inexistentes, bloques vacíos) se devuelven para
    mostrarlos en la misma pantalla: el momento de cazar un dedazo es este,
    no la mañana siguiente con 25 destinatarios delante.
    """
    avisos = config_service.guardar_textos(db, nuevos, usuario=usuario.Username)
    textos = config_service.obtener_textos(db)
    return {
        "textos": textos,
        "avisos": avisos,
        "estructura": email_service.estructura_correos(textos),
        "vista_previa": _vista_previa(textos),
    }


@reminders_router.get("/borrador-convocatoria/{employee_id}")
def borrador_convocatoria(employee_id: int, db: Session = Depends(get_db)):
    """Convocatoria ya redactada para un empleado, lista para revisar y enviar.

    Antes este texto se componía en el frontend, así que había DOS redacciones
    de la misma carta — una en Angular y otra aquí, sin usar — y editar la
    plantilla no habría cambiado la que de verdad salía.
    """
    from app.models.models import Employee, VacationBalance

    emp = db.query(Employee).filter(Employee.Id == employee_id).first()
    if emp is None:
        raise HTTPException(status_code=404, detail="El empleado no existe")

    balance = (db.query(VacationBalance)
                 .filter(VacationBalance.EmployeeId == employee_id)
                 .order_by(VacationBalance.CalculationDate.desc())
                 .first())
    pendientes = balance.PendingByYear if balance else 0
    truncos = balance.PendingTruncated if balance else 0
    depto = emp.department.Name if emp.department else ''

    contenido = email_service.generate_hr_meeting_email(
        emp.FullName, depto, pendientes, truncos,
        textos=config_service.obtener_textos(db))

    return {
        "To": emp.Email or "",
        "Subject": contenido["subject"],
        "Body": contenido["body"],
        "EmployeeId": emp.Id,
        "ReminderType": "hr_meeting",
        "SendNow": True,
    }


@reminders_router.post("/enviar/{employee_id}")
async def enviar_recordatorio_individual(
    employee_id: int,
    db: Session = Depends(get_db),
):
    """Envía el recordatorio a UN trabajador concreto.

    No aplica la ventana de 15 días: es una acción deliberada de RRHH.
    """
    return await reminder_service.enviar_a_empleado(db, employee_id)


@reminders_router.get("/estado-correo")
async def estado_correo():
    """Comprueba que el servidor de correo responde, sin enviar nada.

    Distingue "mal configurado" de "no había a quién enviar" -- justo lo que
    antes no se podía saber, porque el envío se saltaba en silencio.
    """
    ok, error = await email_service.probar_conexion()
    return {
        "habilitado": settings.smtp_enabled,
        "servidor": f"{settings.smtp_host}:{settings.smtp_port}" if settings.smtp_host else None,
        "remitente": f"{settings.smtp_from_name} <{settings.smtp_from_email}>",
        "conecta": ok,
        "error": error,
    }


@reminders_router.post("/send-email")
async def send_custom_email(data: EmailDraft, db: Session = Depends(get_db)):
    """Envía un correo puntual (extensión, convocatoria de RRHH...)."""
    if not data.SendNow:
        return {"message": "Borrador preparado", "draft": data.model_dump()}

    async with email_service.SesionSmtp() as smtp:
        # Misma regla que en las otras vías: si lo que falla es la
        # CONFIGURACION -- interruptor apagado, relay caido -- no se escribe
        # fila. No hubo intento de envio a esta persona que registrar.
        if not smtp.disponible:
            return {"message": f"No se envió: {smtp.error_conexion}",
                    "success": False, "error": smtp.error_conexion}
        ok, error = await smtp.enviar(data.To, data.Subject, data.Body)

    # Queda registrado igual que los recordatorios automáticos: si RRHH
    # convoca a alguien, tiene que poder demostrar que el correo salió.
    if data.EmployeeId:
        vacation_service.create_reminder(
            db, data.EmployeeId, _tipo_valido(data.ReminderType),
            data.To, data.Subject, data.Body,
            status="sent" if ok else "failed",
            sent_at=datetime.now() if ok else None,
            error=error,
        )

    return {
        "message": "Correo enviado" if ok else f"Error al enviar: {error}",
        "success": ok,
        "error": error,
    }
