"""CRUD de usuarios. Solo accesible por administradores."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import User
from app.schemas.schemas import (
    UserCreate, UserUpdate, UserResponse, PasswordResetRequest,
)
from app.services import user_service
from app.auth.dependencies import require_admin


users_router = APIRouter(
    prefix="/api/users",
    tags=["Users"],
    dependencies=[Depends(require_admin)],  # todas las rutas requieren admin
)


@users_router.get("", response_model=list[UserResponse])
def list_users(
    is_active: Optional[bool] = None,
    role: Optional[str] = Query(None, description="admin | gestor | trabajador"),
    search: Optional[str] = None,
    db: Session = Depends(get_db),
):
    return user_service.list_users(db, is_active=is_active, role=role, search=search)


@users_router.get("/{user_id}", response_model=UserResponse)
def get_user(user_id: int, db: Session = Depends(get_db)):
    user = user_service.get_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return user


@users_router.post("", response_model=UserResponse, status_code=201)
def create_user(data: UserCreate, db: Session = Depends(get_db)):
    try:
        return user_service.create_user(db, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@users_router.put("/{user_id}", response_model=UserResponse)
def update_user(user_id: int, data: UserUpdate, db: Session = Depends(get_db)):
    try:
        user = user_service.update_user(db, user_id, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return user


@users_router.post("/{user_id}/reset-password", response_model=UserResponse)
def reset_password(
    user_id: int, data: PasswordResetRequest, db: Session = Depends(get_db)
):
    """Admin resetea la password de otro usuario (no requiere la actual)."""
    try:
        user = user_service.reset_password(db, user_id, data.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return user


@users_router.post("/{user_id}/activate", response_model=UserResponse)
def activate_user(user_id: int, db: Session = Depends(get_db)):
    user = user_service.set_active(db, user_id, True)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return user


@users_router.post("/{user_id}/deactivate", response_model=UserResponse)
def deactivate_user(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if user_id == admin.Id:
        raise HTTPException(status_code=400, detail="No puedes desactivarte a ti mismo")
    user = user_service.set_active(db, user_id, False)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return user
