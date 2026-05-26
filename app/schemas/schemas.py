"""Pydantic schemas for API request/response validation."""
from pydantic import BaseModel, EmailStr
from datetime import date, datetime
from typing import Optional
from decimal import Decimal


# ─── Department ──────────────────────────────────────────────────
class DepartmentBase(BaseModel):
    Name: str

class DepartmentResponse(DepartmentBase):
    Id: int
    IsActive: bool
    class Config:
        from_attributes = True


# ─── Employee ────────────────────────────────────────────────────
class EmployeeCreate(BaseModel):
    Dni: str
    FullName: str
    Email: Optional[str] = None
    DepartmentId: int
    Position: Optional[str] = None
    HireDate: date
    CeaseDate: Optional[date] = None
    DaysPerYear: int = 30

class EmployeeUpdate(BaseModel):
    FullName: Optional[str] = None
    Email: Optional[str] = None
    DepartmentId: Optional[int] = None
    Position: Optional[str] = None
    HireDate: Optional[date] = None
    CeaseDate: Optional[date] = None
    DaysPerYear: Optional[int] = None
    IsActive: Optional[bool] = None

class EmployeeResponse(BaseModel):
    Id: int
    Num: int
    Dni: str
    FullName: str
    Email: Optional[str]
    DepartmentId: int
    DepartmentName: Optional[str] = None
    Position: Optional[str]
    HireDate: date
    CeaseDate: Optional[date]
    DaysPerYear: int
    IsActive: bool
    class Config:
        from_attributes = True

class EmployeeWithBalance(EmployeeResponse):
    YearsWorked: int = 0
    MonthsWorked: int = 0
    DaysWorked: int = 0
    EarnedDays: Decimal = Decimal("0")
    TruncatedDays: Decimal = Decimal("0")
    TakenDays: Decimal = Decimal("0")
    TakenDays2026: Decimal = Decimal("0")
    PendingByYear: Decimal = Decimal("0")
    PendingTruncated: Decimal = Decimal("0")
    TotalPending: Decimal = Decimal("0")
    Vacations: list["VacationResponse"] = []


# ─── Vacation ────────────────────────────────────────────────────
class VacationCreate(BaseModel):
    EmployeeId: int
    StartDate: date
    EndDate: date
    Days: Decimal
    Status: str = "approved"
    VacationType: str = "regular"
    Label: Optional[str] = None
    Notes: Optional[str] = None
    ApprovedBy: Optional[str] = None

class VacationUpdate(BaseModel):
    StartDate: Optional[date] = None
    EndDate: Optional[date] = None
    Days: Optional[Decimal] = None
    Status: Optional[str] = None
    VacationType: Optional[str] = None
    Label: Optional[str] = None
    Notes: Optional[str] = None
    ApprovedBy: Optional[str] = None

class VacationResponse(BaseModel):
    Id: int
    EmployeeId: int
    StartDate: date
    EndDate: date
    Days: Decimal
    Status: str
    VacationType: str
    Label: Optional[str]
    Notes: Optional[str]
    ApprovedBy: Optional[str]
    CreatedAt: datetime
    class Config:
        from_attributes = True

class VacationExtend(BaseModel):
    ExtraDays: int
    Notes: Optional[str] = None


# ─── Reminder ────────────────────────────────────────────────────
class ReminderCreate(BaseModel):
    EmployeeId: int
    ReminderType: str = "pending_30days"
    EmailTo: Optional[str] = None
    EmailSubject: Optional[str] = None
    EmailBody: Optional[str] = None

class ReminderResponse(BaseModel):
    Id: int
    EmployeeId: int
    EmployeeName: Optional[str] = None
    ReminderDate: date
    ReminderType: str
    EmailTo: Optional[str]
    EmailSubject: Optional[str]
    SentAt: Optional[datetime]
    Status: str
    CreatedAt: datetime
    TotalPending: Optional[float] = 0
    PendingByYear: Optional[float] = 0
    PendingTruncated: Optional[float] = 0
    class Config:
        from_attributes = True


# ─── Email Draft ─────────────────────────────────────────────────
class EmailDraft(BaseModel):
    To: str
    Subject: str
    Body: str
    SendNow: bool = False


# ─── Dashboard ───────────────────────────────────────────────────
class DashboardStats(BaseModel):
    TotalEmployees: int
    OnVacation: int
    TotalPendingDays: Decimal
    CriticalAlerts: int
    NoProgrammed: int

class DashboardAlert(BaseModel):
    AlertType: str
    EmployeeId: int
    EmployeeName: str
    Department: str
    Email: Optional[str] = None
    TotalPending: Optional[Decimal] = None
    PendingByYear: Optional[Decimal] = None
    PendingTruncated: Optional[Decimal] = None
    VacationId: Optional[int] = None
    StartDate: Optional[date] = None
    EndDate: Optional[date] = None
    Days: Optional[Decimal] = None

class DashboardResponse(BaseModel):
    Stats: DashboardStats
    Critical: list[DashboardAlert]
    Pending30: list[DashboardAlert]
    NextWeekOut: list[DashboardAlert]
    NextWeekReturn: list[DashboardAlert]
    InProgress: list[DashboardAlert]
    Advanced: list[DashboardAlert]


# ─── Report ──────────────────────────────────────────────────────
class MonthlyReportRow(BaseModel):
    Month: int
    MonthName: str
    EmployeeCount: int
    TotalDays: Decimal

class DepartmentReportRow(BaseModel):
    Department: str
    EmployeeCount: int
    TotalPending: Decimal
    AveragePending: Decimal
    TakenDays2026: Decimal
    CompliancePercent: Decimal

class ProjectionRow(BaseModel):
    EmployeeId: int
    EmployeeName: str
    Department: str
    Position: Optional[str]
    TotalPending: Decimal
    MonthlySchedule: dict[int, Decimal]  # month -> days


# ─── Auth / Users ────────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # segundos
    user: "UserResponse"

class UserResponse(BaseModel):
    Id: int
    Username: str
    Email: str
    FullName: str
    Role: str
    IsActive: bool
    LastLoginAt: Optional[datetime] = None
    CreatedAt: datetime
    class Config:
        from_attributes = True

class UserCreate(BaseModel):
    Username: str
    Email: EmailStr
    FullName: str
    Password: str
    Role: str = "gestor"   # "admin" | "gestor" | "trabajador"
    IsActive: bool = True

class UserUpdate(BaseModel):
    Email: Optional[EmailStr] = None
    FullName: Optional[str] = None
    Role: Optional[str] = None
    IsActive: Optional[bool] = None

class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str

class PasswordResetRequest(BaseModel):
    """Admin resetea password de otro usuario."""
    new_password: str


# ─── Generic ─────────────────────────────────────────────────────
class MessageResponse(BaseModel):
    message: str
    success: bool = True

class PaginatedResponse(BaseModel):
    items: list
    total: int
    page: int
    pageSize: int
    totalPages: int
