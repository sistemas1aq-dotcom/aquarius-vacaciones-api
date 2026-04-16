"""SQLAlchemy ORM models."""
from sqlalchemy import (
    Column, Integer, String, Date, DateTime, Boolean, Numeric,
    ForeignKey, Text, func
)
from sqlalchemy.orm import relationship
from app.database import Base


class Department(Base):
    __tablename__ = "Departments"

    Id = Column(Integer, primary_key=True, autoincrement=True)
    Name = Column(String(100), nullable=False, unique=True)
    IsActive = Column(Boolean, nullable=False, default=True)
    CreatedAt = Column(DateTime, nullable=False, server_default=func.now())
    UpdatedAt = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    employees = relationship("Employee", back_populates="department")


class Employee(Base):
    __tablename__ = "Employees"

    Id = Column(Integer, primary_key=True, autoincrement=True)
    Num = Column(Integer, nullable=False)
    Dni = Column(String(20), nullable=False, unique=True)
    FullName = Column(String(200), nullable=False)
    Email = Column(String(200))
    DepartmentId = Column(Integer, ForeignKey("Departments.Id"), nullable=False)
    Position = Column(String(200))
    HireDate = Column(Date, nullable=False)
    CeaseDate = Column(Date)
    DaysPerYear = Column(Integer, nullable=False, default=30)
    IsActive = Column(Boolean, nullable=False, default=True)
    CreatedAt = Column(DateTime, nullable=False, server_default=func.now())
    UpdatedAt = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    department = relationship("Department", back_populates="employees")
    vacations = relationship("Vacation", back_populates="employee",
                             order_by="Vacation.StartDate.desc()")
    balances = relationship("VacationBalance", back_populates="employee")
    reminders = relationship("VacationReminder", back_populates="employee")


class Vacation(Base):
    __tablename__ = "Vacations"

    Id = Column(Integer, primary_key=True, autoincrement=True)
    EmployeeId = Column(Integer, ForeignKey("Employees.Id"), nullable=False)
    StartDate = Column(Date, nullable=False)
    EndDate = Column(Date, nullable=False)
    Days = Column(Numeric(10, 2), nullable=False)
    Status = Column(String(20), nullable=False, default="approved")
    VacationType = Column(String(20), nullable=False, default="regular")
    Label = Column(String(200))
    Notes = Column(String(500))
    ApprovedBy = Column(String(200))
    CreatedAt = Column(DateTime, nullable=False, server_default=func.now())
    UpdatedAt = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    employee = relationship("Employee", back_populates="vacations")


class VacationBalance(Base):
    __tablename__ = "VacationBalances"

    Id = Column(Integer, primary_key=True, autoincrement=True)
    EmployeeId = Column(Integer, ForeignKey("Employees.Id"), nullable=False)
    CalculationDate = Column(Date, nullable=False)
    YearsWorked = Column(Integer, nullable=False, default=0)
    MonthsWorked = Column(Integer, nullable=False, default=0)
    DaysWorked = Column(Integer, nullable=False, default=0)
    EarnedDays = Column(Numeric(10, 2), nullable=False, default=0)
    TruncatedDays = Column(Numeric(10, 2), nullable=False, default=0)
    TakenDays = Column(Numeric(10, 2), nullable=False, default=0)
    TakenDays2026 = Column(Numeric(10, 2), nullable=False, default=0)
    PendingByYear = Column(Numeric(10, 2), nullable=False, default=0)
    PendingTruncated = Column(Numeric(10, 2), nullable=False, default=0)
    TotalPending = Column(Numeric(10, 2), nullable=False, default=0)
    UpdatedAt = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    employee = relationship("Employee", back_populates="balances")


class VacationReminder(Base):
    __tablename__ = "VacationReminders"

    Id = Column(Integer, primary_key=True, autoincrement=True)
    EmployeeId = Column(Integer, ForeignKey("Employees.Id"), nullable=False)
    ReminderDate = Column(Date, nullable=False)
    ReminderType = Column(String(50), nullable=False, default="pending_30days")
    EmailTo = Column(String(200))
    EmailSubject = Column(String(500))
    EmailBody = Column(Text)
    SentAt = Column(DateTime)
    Status = Column(String(20), nullable=False, default="pending")
    CreatedAt = Column(DateTime, nullable=False, server_default=func.now())

    employee = relationship("Employee", back_populates="reminders")


class User(Base):
    """Usuario del sistema para autenticación JWT."""
    __tablename__ = "Users"

    Id = Column(Integer, primary_key=True, autoincrement=True)
    Username = Column(String(50), nullable=False, unique=True)
    Email = Column(String(200), nullable=False, unique=True)
    FullName = Column(String(200), nullable=False)
    PasswordHash = Column(String(255), nullable=False)
    Role = Column(String(20), nullable=False, default="gestor")  # admin | gestor
    IsActive = Column(Boolean, nullable=False, default=True)
    LastLoginAt = Column(DateTime)
    CreatedAt = Column(DateTime, nullable=False, server_default=func.now())
    UpdatedAt = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class AuditLog(Base):
    __tablename__ = "AuditLog"

    Id = Column(Integer, primary_key=True, autoincrement=True)
    TableName = Column(String(100), nullable=False)
    RecordId = Column(Integer, nullable=False)
    Action = Column(String(20), nullable=False)
    OldValues = Column(Text)
    NewValues = Column(Text)
    UserId = Column(String(100))
    CreatedAt = Column(DateTime, nullable=False, server_default=func.now())
