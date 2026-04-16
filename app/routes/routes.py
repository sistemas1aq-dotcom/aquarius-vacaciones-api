"""API Routes for the Vacation Management System."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import date
from typing import Optional
from decimal import Decimal

from app.database import get_db
from app.schemas.schemas import (
    EmployeeCreate, EmployeeUpdate, EmployeeWithBalance,
    VacationCreate, VacationUpdate, VacationExtend, VacationResponse,
    EmailDraft, ReminderResponse, MessageResponse,
    DepartmentResponse,
)
from app.services import employee_service, vacation_service, email_service
from app.auth.dependencies import require_auth

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
    page: int = Query(1, ge=1),
    pageSize: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
):
    items, total = employee_service.get_employees(
        db, department=department, search=search, page=page, page_size=pageSize
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

    def _balance_to_alert(b, alert_type):
        from app.models.models import Employee, Department
        emp = db.query(Employee).join(Department).filter(Employee.Id == b.EmployeeId).first()
        return {
            "AlertType": alert_type,
            "EmployeeId": b.EmployeeId,
            "EmployeeName": emp.FullName if emp else "",
            "Department": emp.department.Name if emp and emp.department else "",
            "Email": emp.Email if emp else "",
            "TotalPending": float(b.TotalPending),
            "PendingByYear": float(b.PendingByYear),
            "PendingTruncated": float(b.PendingTruncated),
        }

    def _vac_to_alert(v, alert_type):
        return {
            "AlertType": alert_type,
            "EmployeeId": v.EmployeeId,
            "EmployeeName": v.employee.FullName if v.employee else "",
            "Department": v.employee.department.Name if v.employee and v.employee.department else "",
            "VacationId": v.Id,
            "StartDate": v.StartDate.isoformat() if v.StartDate else None,
            "EndDate": v.EndDate.isoformat() if v.EndDate else None,
            "Days": float(v.Days),
        }

    return {
        "Stats": data["stats"],
        "Critical": [_balance_to_alert(b, "critical") for b in data["critical"]],
        "Pending30": [_balance_to_alert(b, "pending_30") for b in data["pending_30"]],
        "NextWeekOut": [_vac_to_alert(v, "next_week_out") for v in data["next_out"]],
        "NextWeekReturn": [_vac_to_alert(v, "next_week_return") for v in data["next_return"]],
        "InProgress": [_vac_to_alert(v, "in_progress") for v in data["in_progress"]],
        "Advanced": [_balance_to_alert(b, "advanced") for b in data["advanced"]],
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


@reminders_router.post("/send-daily")
async def send_daily_reminders(db: Session = Depends(get_db)):
    """Send reminders to all employees with 30+ pending days and no scheduled vacations."""
    from app.config import get_settings
    settings = get_settings()

    employees = vacation_service.get_employees_needing_reminders(
        db, settings.reminder_threshold_days
    )

    sent_count = 0
    for emp, balance in employees:
        email_data = email_service.generate_reminder_email(
            emp.FullName, balance.TotalPending,
            balance.PendingByYear, balance.PendingTruncated
        )

        # Create reminder record
        vacation_service.create_reminder(
            db, emp.Id, "pending_30days",
            emp.Email, email_data["subject"], email_data["body"]
        )

        # Send email
        if emp.Email and settings.smtp_user:
            success = await email_service.send_email(
                emp.Email, email_data["subject"], email_data["body"]
            )
            if success:
                sent_count += 1

    return {
        "message": f"Recordatorios procesados: {len(employees)}. Enviados: {sent_count}.",
        "total": len(employees),
        "sent": sent_count,
    }


@reminders_router.post("/send-email")
async def send_custom_email(data: EmailDraft, db: Session = Depends(get_db)):
    """Send a custom email (extension, HR meeting, etc.)."""
    if data.SendNow:
        success = await email_service.send_email(data.To, data.Subject, data.Body)
        return {"message": "Correo enviado" if success else "Error al enviar", "success": success}

    return {"message": "Borrador preparado", "draft": data.model_dump()}
