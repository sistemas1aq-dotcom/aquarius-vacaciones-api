"""Vacation service - CRUD and business logic."""
from sqlalchemy.orm import Session
from sqlalchemy import text, func
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional
from app.models.models import Vacation, Employee, VacationReminder
from app.schemas.schemas import VacationCreate, VacationUpdate


def get_vacations_by_employee(db: Session, employee_id: int):
    """Get all vacations for an employee."""
    return (
        db.query(Vacation)
        .filter(Vacation.EmployeeId == employee_id)
        .order_by(Vacation.StartDate.desc())
        .all()
    )


def create_vacation(db: Session, data: VacationCreate) -> Vacation:
    """Create a new vacation record."""
    vac = Vacation(
        EmployeeId=data.EmployeeId,
        StartDate=data.StartDate,
        EndDate=data.EndDate,
        Days=data.Days,
        Status=data.Status,
        VacationType=data.VacationType,
        Label=data.Label,
        Notes=data.Notes,
        ApprovedBy=data.ApprovedBy,
    )
    db.add(vac)
    db.commit()
    db.refresh(vac)

    # Recalculate balance
    _recalculate_balance(db, data.EmployeeId)
    return vac


def update_vacation(db: Session, vacation_id: int, data: VacationUpdate) -> Optional[Vacation]:
    """Update an existing vacation record."""
    vac = db.query(Vacation).filter(Vacation.Id == vacation_id).first()
    if not vac:
        return None

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(vac, key, value)

    vac.UpdatedAt = func.now()
    db.commit()
    db.refresh(vac)

    # Recalculate balance
    _recalculate_balance(db, vac.EmployeeId)
    return vac


def delete_vacation(db: Session, vacation_id: int) -> bool:
    """Delete a vacation record."""
    vac = db.query(Vacation).filter(Vacation.Id == vacation_id).first()
    if not vac:
        return False

    emp_id = vac.EmployeeId
    db.delete(vac)
    db.commit()

    # Recalculate balance
    _recalculate_balance(db, emp_id)
    return True


def extend_vacation(db: Session, vacation_id: int, extra_days: int, notes: str = None) -> Optional[Vacation]:
    """Extend a vacation by adding extra days."""
    vac = db.query(Vacation).filter(Vacation.Id == vacation_id).first()
    if not vac:
        return None

    new_end = vac.EndDate + timedelta(days=extra_days)
    vac.EndDate = new_end
    vac.Days = vac.Days + extra_days
    if notes:
        vac.Notes = f"{vac.Notes or ''}\nExtensión: +{extra_days} días. {notes}".strip()
    vac.UpdatedAt = func.now()

    db.commit()
    db.refresh(vac)

    _recalculate_balance(db, vac.EmployeeId)
    return vac


def _recalculate_balance(db: Session, employee_id: int):
    """Recalculate vacation balance via PostgreSQL function."""
    try:
        db.execute(
            text("SELECT sp_calculate_vacation_balance(:emp_id, :calc_date)"),
            {"emp_id": employee_id, "calc_date": date.today()},
        )
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[WARN] _recalculate_balance failed for {employee_id}: {e}")


def get_employees_needing_reminders(db: Session, threshold_days: int = 30):
    """Get employees with pending days above threshold who need reminders."""
    from app.models.models import VacationBalance, Employee, Department

    today_date = date.today()
    results = (
        db.query(Employee, VacationBalance)
        .join(VacationBalance, VacationBalance.EmployeeId == Employee.Id)
        .join(Department, Employee.DepartmentId == Department.Id)
        .filter(
            VacationBalance.CalculationDate == today_date,
            VacationBalance.TotalPending > threshold_days,
            Employee.IsActive == True,
        )
        .all()
    )

    # Check if they have future scheduled vacations
    needing = []
    for emp, balance in results:
        has_scheduled = (
            db.query(Vacation)
            .filter(
                Vacation.EmployeeId == emp.Id,
                Vacation.Status == "approved",
                Vacation.StartDate > today_date,
            )
            .first()
        )
        if not has_scheduled:
            needing.append((emp, balance))

    return needing


def create_reminder(db: Session, employee_id: int, reminder_type: str,
                    email_to: str, subject: str, body: str) -> VacationReminder:
    """Create a reminder record."""
    reminder = VacationReminder(
        EmployeeId=employee_id,
        ReminderDate=date.today(),
        ReminderType=reminder_type,
        EmailTo=email_to,
        EmailSubject=subject,
        EmailBody=body,
        Status="pending",
    )
    db.add(reminder)
    db.commit()
    db.refresh(reminder)
    return reminder


def get_reminders(db: Session, page: int = 1, page_size: int = 100):
    """Get paginated reminder history (includes employee's current pending days)."""
    from app.models.models import VacationBalance

    query = (
        db.query(VacationReminder)
        .join(Employee)
        .order_by(VacationReminder.CreatedAt.desc())
    )
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()

    results = []
    for r in items:
        emp = db.query(Employee).filter(Employee.Id == r.EmployeeId).first()
        # Latest vacation balance snapshot (current pending days)
        balance = (
            db.query(VacationBalance)
            .filter(VacationBalance.EmployeeId == r.EmployeeId)
            .order_by(VacationBalance.CalculationDate.desc())
            .first()
        )
        results.append({
            "Id": r.Id,
            "EmployeeId": r.EmployeeId,
            "EmployeeName": emp.FullName if emp else "",
            "ReminderDate": r.ReminderDate,
            "ReminderType": r.ReminderType,
            "EmailTo": r.EmailTo,
            "EmailSubject": r.EmailSubject,
            "SentAt": r.SentAt,
            "Status": r.Status,
            "CreatedAt": r.CreatedAt,
            "TotalPending": float(balance.TotalPending) if balance else 0,
            "PendingByYear": float(balance.PendingByYear) if balance else 0,
            "PendingTruncated": float(balance.PendingTruncated) if balance else 0,
        })

    return results, total
