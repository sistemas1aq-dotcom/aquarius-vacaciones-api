"""Employee service - business logic layer."""
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, text, and_
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional
from app.models.models import Employee, Department, Vacation, VacationBalance
from app.schemas.schemas import EmployeeCreate, EmployeeUpdate


MONTH_NAMES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
    5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
    9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}


def get_employees(
    db: Session,
    department: Optional[str] = None,
    search: Optional[str] = None,
    is_active: Optional[bool] = True,
    page: int = 1,
    page_size: int = 200,
):
    """Get paginated list of employees with balances.

    is_active:
      - True  → solo activos (por defecto)
      - False → solo inactivos
      - None  → todos (activos + inactivos)
    """
    query = (
        db.query(Employee)
        .join(Department)
        .options(joinedload(Employee.department))
    )
    if is_active is not None:
        query = query.filter(Employee.IsActive == is_active)

    if department:
        query = query.filter(Department.Name == department)

    if search:
        search_term = f"%{search}%"
        query = query.filter(
            (Employee.FullName.ilike(search_term))
            | (Employee.Dni.ilike(search_term))
            | (Employee.Position.ilike(search_term))
        )

    total = query.count()
    employees = (
        query.order_by(Employee.FullName)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    # Attach balances
    today = date.today()
    results = []
    for emp in employees:
        balance = (
            db.query(VacationBalance)
            .filter(
                VacationBalance.EmployeeId == emp.Id,
                VacationBalance.CalculationDate == today,
            )
            .first()
        )

        emp_dict = {
            "Id": emp.Id,
            "Num": emp.Num,
            "Dni": emp.Dni,
            "FullName": emp.FullName,
            "Email": emp.Email,
            "DepartmentId": emp.DepartmentId,
            "DepartmentName": emp.department.Name if emp.department else None,
            "Position": emp.Position,
            "HireDate": emp.HireDate,
            "CeaseDate": emp.CeaseDate,
            "DaysPerYear": emp.DaysPerYear,
            "IsActive": emp.IsActive,
        }

        if balance:
            emp_dict.update({
                "YearsWorked": balance.YearsWorked,
                "MonthsWorked": balance.MonthsWorked,
                "DaysWorked": balance.DaysWorked,
                "EarnedDays": balance.EarnedDays,
                "TruncatedDays": balance.TruncatedDays,
                "TakenDays": balance.TakenDays,
                "TakenDays2026": balance.TakenDays2026,
                "PendingByYear": balance.PendingByYear,
                "PendingTruncated": balance.PendingTruncated,
                "TotalPending": balance.TotalPending,
            })

        results.append(emp_dict)

    return results, total


def get_employee_detail(db: Session, employee_id: int):
    """Get employee with full vacation history and balance."""
    emp = (
        db.query(Employee)
        .options(joinedload(Employee.department), joinedload(Employee.vacations))
        .filter(Employee.Id == employee_id)
        .first()
    )
    if not emp:
        return None

    # Recalculate balance
    db.execute(
        text("EXEC dbo.sp_calculate_vacation_balance "
             "@p_employee_id = :emp_id, @p_calculation_date = :calc_date"),
        {"emp_id": employee_id, "calc_date": date.today()},
    )
    db.commit()

    balance = (
        db.query(VacationBalance)
        .filter(
            VacationBalance.EmployeeId == employee_id,
            VacationBalance.CalculationDate == date.today(),
        )
        .first()
    )

    result = {
        "Id": emp.Id,
        "Num": emp.Num,
        "Dni": emp.Dni,
        "FullName": emp.FullName,
        "Email": emp.Email,
        "DepartmentId": emp.DepartmentId,
        "DepartmentName": emp.department.Name if emp.department else None,
        "Position": emp.Position,
        "HireDate": emp.HireDate,
        "CeaseDate": emp.CeaseDate,
        "DaysPerYear": emp.DaysPerYear,
        "IsActive": emp.IsActive,
        "YearsWorked": balance.YearsWorked if balance else 0,
        "MonthsWorked": balance.MonthsWorked if balance else 0,
        "DaysWorked": balance.DaysWorked if balance else 0,
        "EarnedDays": balance.EarnedDays if balance else Decimal("0"),
        "TruncatedDays": balance.TruncatedDays if balance else Decimal("0"),
        "TakenDays": balance.TakenDays if balance else Decimal("0"),
        "TakenDays2026": balance.TakenDays2026 if balance else Decimal("0"),
        "PendingByYear": balance.PendingByYear if balance else Decimal("0"),
        "PendingTruncated": balance.PendingTruncated if balance else Decimal("0"),
        "TotalPending": balance.TotalPending if balance else Decimal("0"),
        "Vacations": [
            {
                "Id": v.Id,
                "EmployeeId": v.EmployeeId,
                "StartDate": v.StartDate,
                "EndDate": v.EndDate,
                "Days": v.Days,
                "Status": v.Status,
                "VacationType": v.VacationType,
                "Label": v.Label,
                "Notes": v.Notes,
                "ApprovedBy": v.ApprovedBy,
                "CreatedAt": v.CreatedAt,
            }
            for v in emp.vacations
        ],
    }
    return result


def create_employee(db: Session, data: EmployeeCreate) -> Employee:
    """Create a new employee."""
    max_num = db.query(func.max(Employee.Num)).scalar() or 0
    emp = Employee(
        Num=max_num + 1,
        Dni=data.Dni,
        FullName=data.FullName,
        Email=data.Email,
        DepartmentId=data.DepartmentId,
        Position=data.Position,
        HireDate=data.HireDate,
        CeaseDate=data.CeaseDate,
        DaysPerYear=data.DaysPerYear,
    )
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


def update_employee(db: Session, employee_id: int, data: EmployeeUpdate) -> Optional[Employee]:
    """Update an existing employee."""
    emp = db.query(Employee).filter(Employee.Id == employee_id).first()
    if not emp:
        return None

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(emp, key, value)

    emp.UpdatedAt = func.now()
    db.commit()
    db.refresh(emp)
    return emp


def get_departments(db: Session) -> list[Department]:
    """Get all active departments."""
    return db.query(Department).filter(Department.IsActive == True).order_by(Department.Name).all()


def get_dashboard_data(db: Session):
    """Get dashboard statistics and alerts."""
    today_date = date.today()
    next_week_start = today_date + timedelta(days=1)
    next_week_end = today_date + timedelta(days=7)

    # Recalculate all balances
    db.execute(
        text("EXEC dbo.sp_recalculate_all_balances @p_calculation_date = :calc_date"),
        {"calc_date": today_date},
    )
    db.commit()

    # Stats
    total_emps = db.query(Employee).filter(Employee.IsActive == True).count()

    in_progress = (
        db.query(func.count(func.distinct(Vacation.EmployeeId)))
        .filter(Vacation.Status == "in_progress")
        .scalar()
        or 0
    )

    balances = (
        db.query(VacationBalance)
        .join(Employee)
        .filter(
            VacationBalance.CalculationDate == today_date,
            Employee.IsActive == True,
        )
        .all()
    )

    total_pending = sum(max(Decimal("0"), b.TotalPending) for b in balances)
    critical = [b for b in balances if b.TotalPending > 60]
    pending_30 = [b for b in balances if b.TotalPending > 30]

    # Next week out/return
    next_out = (
        db.query(Vacation)
        .join(Employee)
        .join(Department, Employee.DepartmentId == Department.Id)
        .filter(
            Vacation.Status == "approved",
            Vacation.StartDate.between(next_week_start, next_week_end),
        )
        .all()
    )

    next_return = (
        db.query(Vacation)
        .join(Employee)
        .join(Department, Employee.DepartmentId == Department.Id)
        .filter(
            Vacation.Status.in_(["in_progress", "approved"]),
            Vacation.EndDate.between(next_week_start, next_week_end),
        )
        .all()
    )

    in_prog_list = (
        db.query(Vacation)
        .join(Employee)
        .filter(Vacation.Status == "in_progress")
        .all()
    )

    # Sin programar: mas de 15 dias pendientes y ninguna vacacion futura
    # aprobada.
    #
    # Antes esto lanzaba UNA consulta por empleado dentro del bucle. Con 110
    # trabajadores eran 110 viajes a la base cada vez que alguien abria el
    # dashboard. Ahora se traen de golpe los que si tienen algo programado y
    # se comprueba contra un conjunto en memoria.
    con_futuras = {
        fila[0] for fila in db.query(Vacation.EmployeeId)
        .filter(Vacation.Status == "approved", Vacation.StartDate > today_date)
        .distinct()
        .all()
    }
    sin_programar = [b for b in balances
                     if b.TotalPending > 15 and b.EmployeeId not in con_futuras]

    # Adelantos: saldo NEGATIVO, es decir, gozo mas dias de los generados.
    # Se calculaba desde siempre y no se pintaba en ninguna parte.
    advanced = sorted([b for b in balances if b.TotalPending < 0],
                      key=lambda b: b.TotalPending)

    # Truncos: derecho en formacion. Desde que dejaron de sumarse al
    # pendiente no aparecen en ninguna pantalla, pero se pagan al cesar: son
    # dinero que la empresa debe y que hoy no esta mirando nadie.
    truncos = sorted([b for b in balances if b.PendingTruncated > 0],
                     key=lambda b: b.PendingTruncated, reverse=True)
    truncos_total = sum(b.PendingTruncated for b in truncos)

    # Sin correo registrado: a esta gente NO se le puede avisar por ninguna
    # via, ni automatica ni manual. Es la lista mas accionable del tablero.
    sin_correo = (
        db.query(Employee)
        .join(Department, Employee.DepartmentId == Department.Id)
        .filter(Employee.IsActive == True,  # noqa: E712
        func.coalesce(func.ltrim(func.rtrim(Employee.Email)), "") == "")
        .order_by(Employee.FullName)
        .all()
    )

    # Correos fallidos del ultimo mes: salud del envio. Si el relay se cae o
    # un buzon deja de existir, hoy no se entera nadie hasta entrar a
    # Recordatorios.
    from app.models.models import VacationReminder
    desde_correos = today_date - timedelta(days=30)
    correos_fallidos = (
        db.query(VacationReminder)
        .filter(VacationReminder.Status == "failed",
                VacationReminder.ReminderDate >= desde_correos)
        .order_by(VacationReminder.ReminderDate.desc())
        .all()
    )

    # Pico de ausencias del proximo mes: cuanta gente coincide fuera el dia
    # de mayor solape. Es la pregunta de planificacion que ninguna pantalla
    # responde -- el mapa de calor la tiene delante pero no la resume.
    pico = _pico_ausencias(db, today_date)

    return {
        "stats": {
            "TotalEmployees": total_emps,
            "OnVacation": in_progress,
            "TotalPendingDays": total_pending,
            "CriticalAlerts": len(critical),
            "NoProgrammed": len(sin_programar),
            "TruncatedDays": truncos_total,
            "NoEmail": len(sin_correo),
            "FailedEmails": len(correos_fallidos),
            "PeakNextMonth": pico["total"],
            "PeakDate": pico["fecha"],
        },
        "critical": critical,
        "pending_30": pending_30,
        "next_out": next_out,
        "next_return": next_return,
        "in_progress": in_prog_list,
        "advanced": advanced,
        "sin_programar": sin_programar,
        "truncos": truncos,
        # Los que componen la suma de "Dias Pendientes": el numero grande
        # sin la lista detras no se puede auditar.
        "pendientes": sorted([b for b in balances if b.TotalPending > 0],
                             key=lambda b: b.TotalPending, reverse=True),
        "sin_correo": sin_correo,
        "correos_fallidos": correos_fallidos,
        "pico": pico,
        "balances": {b.EmployeeId: b for b in balances},
    }


def _pico_ausencias(db, hoy):
    """Dia del proximo mes con mas gente de vacaciones a la vez.

    Se recorren los dias del mes contando cuantas vacaciones lo cubren. Con
    31 dias y unas decenas de vacaciones el coste es irrelevante, y evita
    tener que explicar una consulta de solapamientos en SQL.
    """
    inicio = (hoy.replace(day=1) + timedelta(days=32)).replace(day=1)
    fin = (inicio + timedelta(days=32)).replace(day=1) - timedelta(days=1)

    vacaciones = (
        db.query(Vacation)
        .join(Employee)
        .filter(Vacation.Status.in_(["approved", "in_progress"]),
                Vacation.StartDate <= fin,
                Vacation.EndDate >= inicio)
        .all()
    )

    mejor_dia, mejor = inicio, []
    dia = inicio
    while dia <= fin:
        hoy_fuera = [v for v in vacaciones if v.StartDate <= dia <= v.EndDate]
        if len(hoy_fuera) > len(mejor):
            mejor_dia, mejor = dia, hoy_fuera
        dia += timedelta(days=1)

    return {
        "fecha": mejor_dia.isoformat() if mejor else None,
        "total": len(mejor),
        "vacaciones": mejor,
        "desde": inicio.isoformat(),
        "hasta": fin.isoformat(),
    }


def get_projection(db: Session, year: int = 2026):
    """Get vacation projection for all employees."""
    today_date = date.today()

    employees = (
        db.query(Employee)
        .join(Department)
        .options(joinedload(Employee.department))
        .filter(Employee.IsActive == True)
        .order_by(Employee.FullName)
        .all()
    )

    results = []
    for emp in employees:
        balance = (
            db.query(VacationBalance)
            .filter(
                VacationBalance.EmployeeId == emp.Id,
                VacationBalance.CalculationDate == today_date,
            )
            .first()
        )

        vacations = (
            db.query(Vacation)
            .filter(
                Vacation.EmployeeId == emp.Id,
                Vacation.StartDate >= date(year, 1, 1),
                Vacation.StartDate <= date(year, 12, 31),
                Vacation.Status.in_(["approved", "in_progress", "completed"]),
            )
            .all()
        )

        monthly = {}
        vac_segments = []
        for v in vacations:
            start_month = v.StartDate.month
            # Si la vacación se cruza al año siguiente, recortamos al año proyectado
            end_month = v.EndDate.month if v.EndDate.year == year else 12
            # Mapa por mes (compat — sigue mostrando totales mensuales)
            monthly[start_month] = monthly.get(start_month, Decimal("0")) + v.Days
            vac_segments.append({
                "VacationId": v.Id,
                "StartMonth": start_month,
                "EndMonth": end_month,
                "StartDate": v.StartDate,
                "EndDate": v.EndDate,
                "Days": v.Days,
                "Label": v.Label,
                "Status": v.Status,
            })

        # Ordenar por fecha de inicio para que el render sea determinista
        vac_segments.sort(key=lambda s: s["StartDate"])

        results.append({
            "EmployeeId": emp.Id,
            "EmployeeName": emp.FullName,
            "Department": emp.department.Name if emp.department else "",
            "Position": emp.Position,
            "TotalPending": balance.TotalPending if balance else Decimal("0"),
            "MonthlySchedule": monthly,
            "Vacations": vac_segments,
        })

    return results


def get_monthly_report(db: Session, year: int = 2026):
    """Get monthly vacation report (compatible con PostgreSQL vía EXTRACT)."""
    from sqlalchemy import extract, cast, Integer
    month_expr = cast(extract("month", Vacation.StartDate), Integer).label("Month")
    year_expr = cast(extract("year", Vacation.StartDate), Integer)
    results = (
        db.query(
            month_expr,
            func.count(func.distinct(Vacation.EmployeeId)).label("EmployeeCount"),
            func.sum(Vacation.Days).label("TotalDays"),
        )
        .filter(
            year_expr == year,
            Vacation.Status.in_(["completed", "in_progress", "approved"]),
        )
        .group_by(month_expr)
        .order_by(month_expr)
        .all()
    )

    return [
        {
            "Month": r.Month,
            "MonthName": MONTH_NAMES.get(r.Month, ""),
            "EmployeeCount": r.EmployeeCount,
            "TotalDays": r.TotalDays or Decimal("0"),
        }
        for r in results
    ]


def get_department_report(db: Session):
    """Get vacation report by department."""
    today_date = date.today()
    departments = db.query(Department).filter(Department.IsActive == True).all()

    results = []
    for dept in departments:
        emps = (
            db.query(Employee)
            .filter(Employee.DepartmentId == dept.Id, Employee.IsActive == True)
            .all()
        )

        if not emps:
            continue

        total_pending = Decimal("0")
        taken_2026 = Decimal("0")
        total_earned = Decimal("0")
        total_taken_all = Decimal("0")

        for emp in emps:
            balance = (
                db.query(VacationBalance)
                .filter(
                    VacationBalance.EmployeeId == emp.Id,
                    VacationBalance.CalculationDate == today_date,
                )
                .first()
            )
            if balance:
                total_pending += max(Decimal("0"), balance.TotalPending)
                taken_2026 += balance.TakenDays2026
                total_earned += balance.EarnedDays + balance.TruncatedDays
                total_taken_all += balance.TakenDays

        emp_count = len(emps)
        compliance = (
            round(total_taken_all / total_earned * 100, 1)
            if total_earned > 0
            else Decimal("0")
        )

        results.append({
            "Department": dept.Name,
            "EmployeeCount": emp_count,
            "TotalPending": round(total_pending, 0),
            "AveragePending": round(total_pending / emp_count, 0) if emp_count else Decimal("0"),
            "TakenDays2026": round(taken_2026, 0),
            "CompliancePercent": compliance,
        })

    return results
