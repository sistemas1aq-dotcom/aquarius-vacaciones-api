"""Rutas de la integración con Planillas.

    POST /api/integraciones/planillas/sync    dispara una corrida
    GET  /api/integraciones/planillas/estado  semáforo + última corrida
    GET  /api/integraciones/planillas/corridas  historial

Todas exigen JWT, igual que el resto de la API. El sync además exige rol admin:
es una operación que escribe sobre el maestro de personal y puede cesar
empleados, no algo que deba poder disparar cualquier gestor.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.models import SyncCorrida, User
from app.services import planillas_service

planillas_router = APIRouter(
    prefix="/api/integraciones/planillas",
    tags=["Integraciones"],
    dependencies=[Depends(get_current_user)],
)


def _exigir_admin(usuario: User) -> None:
    if usuario.Role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo un administrador puede ejecutar la sincronización.",
        )


@planillas_router.post("/sync")
def sincronizar(
    cod_empresa: str | None = Query(default=None, description="Empresa a sincronizar; '0' = todas"),
    limite: int = Query(default=0, ge=0, le=10000,
                        description="Máximo de filas a traer. 0 = todas. Con limite>0 NO se calculan ceses."),
    dry_run: bool = Query(default=False, description="Calcula y reporta, sin escribir nada"),
    db: Session = Depends(get_db),
    usuario: User = Depends(get_current_user),
):
    """Ejecuta una corrida de sincronización de personal desde Planillas."""
    _exigir_admin(usuario)
    return planillas_service.sincronizar(
        db, cod_empresa=cod_empresa, limite=limite, dry_run=dry_run
    )


@planillas_router.get("/estado")
def estado(db: Session = Depends(get_db)):
    """Semáforo de la integración y resumen de la última corrida."""
    return planillas_service.estado_integracion(db)


@planillas_router.get("/corridas")
def corridas(
    limite: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Historial de corridas, de la más reciente a la más antigua."""
    filas = (
        db.query(SyncCorrida)
        .order_by(SyncCorrida.Inicio.desc())
        .limit(limite)
        .all()
    )
    return [
        {
            "id": c.Id,
            "inicio": c.Inicio,
            "fin": c.Fin,
            "cod_empresa": c.CodEmpresa,
            "dry_run": bool(c.DryRun),
            "estado": c.Estado,
            "mensaje": c.Mensaje,
            "total_origen": c.TotalOrigen,
            "en_alcance": c.EnAlcance,
            "altas": c.Altas,
            "actualizaciones": c.Actualizaciones,
            "ceses": c.Ceses,
            "reactivaciones": c.Reactivaciones,
            "ignorados": c.Ignorados,
            "fuera_de_alcance": c.FueraDeAlcance,
            "errores": c.Errores,
        }
        for c in filas
    ]
