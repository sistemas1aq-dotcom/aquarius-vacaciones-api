"""
AQUARIUS - Sistema de Gestión de Vacaciones
FastAPI Backend Entry Point
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import get_settings
from app.routes.routes import (
    employees_router, vacations_router, dashboard_router,
    reports_router, reminders_router, departments_router,
)
from app.routes.auth_routes import auth_router
from app.routes.users_routes import users_router

settings = get_settings()
scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup/shutdown events."""
    if settings.enable_scheduler:
        try:
            scheduler.add_job(
                _daily_reminder_job,
                "cron",
                hour=settings.reminder_cron_hour,
                minute=settings.reminder_cron_minute,
                id="daily_vacation_reminders",
                replace_existing=True,
            )
            scheduler.start()
            print(f"[OK] Scheduler started: daily reminders at "
                  f"{settings.reminder_cron_hour:02d}:{settings.reminder_cron_minute:02d}")
        except Exception as e:
            print(f"[WARN] Scheduler no iniciado: {e}")
    else:
        print("[INFO] Scheduler deshabilitado (ENABLE_SCHEDULER=false)")
    yield
    if scheduler.running:
        scheduler.shutdown()
        print("[STOP] Scheduler stopped")


app = FastAPI(
    title="AQUARIUS - Gestión de Vacaciones",
    description="Sistema integral de administración de vacaciones del personal",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routes
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(dashboard_router)
app.include_router(employees_router)
app.include_router(vacations_router)
app.include_router(reports_router)
app.include_router(reminders_router)
app.include_router(departments_router)


@app.get("/")
def root():
    return {
        "app": "AQUARIUS - Gestión de Vacaciones",
        "version": "1.0.0",
        "status": "running",
    }


@app.get("/api/health")
def health():
    return {"status": "ok"}


async def _daily_reminder_job():
    """Scheduled job: send daily reminders to employees with 30+ pending days."""
    from app.database import SessionLocal
    from app.services import vacation_service, email_service

    db = SessionLocal()
    try:
        employees = vacation_service.get_employees_needing_reminders(
            db, settings.reminder_threshold_days
        )
        for emp, balance in employees:
            email_data = email_service.generate_reminder_email(
                emp.FullName, balance.TotalPending,
                balance.PendingByYear, balance.PendingTruncated,
            )
            vacation_service.create_reminder(
                db, emp.Id, "pending_30days",
                emp.Email, email_data["subject"], email_data["body"],
            )
            if emp.Email and settings.smtp_user:
                await email_service.send_email(
                    emp.Email, email_data["subject"], email_data["body"]
                )
        print(f"📧 Daily reminders sent to {len(employees)} employees")
    except Exception as e:
        print(f"❌ Reminder job error: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_debug,
    )
