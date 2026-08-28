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
from app.routes.planillas_routes import planillas_router

settings = get_settings()
scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup/shutdown events."""
    if settings.enable_scheduler:
        try:
            # El job diario se registra SIEMPRE. Si envía o no lo decide el
            # interruptor «Envíos en lote» en el momento de dispararse, no al
            # arrancar: así se apaga y enciende desde la pantalla, sin
            # reiniciar el servicio. Un job que no envía cuesta una consulta
            # al día; un servicio que hay que reiniciar para callarlo cuesta
            # bastante más.
            scheduler.add_job(
                _daily_reminder_job,
                "cron",
                hour=settings.reminder_cron_hour,
                minute=settings.reminder_cron_minute,
                id="daily_vacation_reminders",
                replace_existing=True,
            )
            # Sincronización de personal desde Planillas.
            # Se apoya en el mismo scheduler que ya existía: no hace falta
            # tarea programada de Windows, script PowerShell ni usuario de
            # servicio. Arranca desactivado; se habilita tras validar con
            # dry_run (PLANILLAS_SYNC_ENABLED=true).
            if settings.planillas_sync_enabled:
                scheduler.add_job(
                    _planillas_sync_job,
                    "interval",
                    minutes=settings.planillas_intervalo_min,
                    id="planillas_sync_personal",
                    replace_existing=True,
                    max_instances=1,   # que una corrida lenta no se solape
                    coalesce=True,     # si se acumulan disparos, ejecuta uno
                )

            scheduler.start()
            print(f"[OK] Corrida diaria de recordatorios programada a las "
                  f"{settings.reminder_cron_hour:02d}:{settings.reminder_cron_minute:02d} "
                  f"(enviará solo si el interruptor «Envíos en lote» está activo)")
            if settings.planillas_sync_enabled:
                print(f"[OK] Sync de Planillas cada {settings.planillas_intervalo_min} min")
            else:
                print("[INFO] Sync de Planillas desactivado (PLANILLAS_SYNC_ENABLED=false)")
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
# Si APP_CORS_ORIGINS=* abrimos a cualquier origen via regex (compatible con allow_credentials=True;
# el navegador rechaza '*' literal cuando hay credenciales, asi que reflejamos el Origin real).
_cors_kwargs = {
    "allow_credentials": True,
    "allow_methods": ["*"],
    "allow_headers": ["*"],
}
if settings.cors_origins == ["*"]:
    _cors_kwargs["allow_origin_regex"] = ".*"
else:
    _cors_kwargs["allow_origins"] = settings.cors_origins

app.add_middleware(CORSMiddleware, **_cors_kwargs)

# Register routes
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(planillas_router)
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


async def _planillas_sync_job():
    """Corrida programada del sync de personal.

    Nunca lanza: un fallo aquí no debe tumbar el scheduler ni impedir que
    corran los recordatorios. El detalle del error queda en SyncCorrida.
    """
    from app.database import SessionLocal
    from app.services import planillas_service

    db = SessionLocal()
    try:
        r = planillas_service.sincronizar(db)
        print(f"[SYNC] {r['estado']}  altas={r['altas']} updates={r['actualizaciones']} "
              f"ceses={r['ceses']} ignorados={r['ignorados']} fuera={r['fuera_de_alcance']}")
        if r["mensaje"]:
            print(f"[SYNC] {r['mensaje']}")
    except Exception as e:
        print(f"[SYNC] Error no controlado: {e}")
    finally:
        db.close()


async def _daily_reminder_job():
    """Corrida diaria de recordatorios.

    Toda la lógica vive en `reminder_service`, el mismo que usa el endpoint
    manual: antes estaba duplicada aquí y en la ruta, con el riesgo de que
    las dos copias divergieran.
    """
    from app.database import SessionLocal
    from app.services import reminder_service

    db = SessionLocal()
    try:
        r = await reminder_service.enviar_recordatorios_diarios(db)
        print(f"[RECORDATORIOS] {r['mensaje']}")
        for f in r["detalle"]["fallidos"][:5]:
            print(f"[RECORDATORIOS] fallo con {f['empleado']}: {f['motivo']}")
    except Exception as e:  # noqa: BLE001 - no puede tumbar el scheduler
        print(f"[RECORDATORIOS] Error no controlado: {e}")
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
