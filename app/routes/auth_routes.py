"""Rutas de autenticación: login, perfil actual, cambiar password."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import User
from app.schemas.schemas import (
    LoginRequest, TokenResponse, UserResponse, PasswordChangeRequest,
)
from app.services import user_service
from app.auth.security import create_access_token
from app.auth.dependencies import get_current_user


auth_router = APIRouter(prefix="/api/auth", tags=["Auth"])


@auth_router.post("/login", response_model=TokenResponse)
def login(data: LoginRequest, db: Session = Depends(get_db)):
    """Login con username (o email) y password. Retorna JWT."""
    user = user_service.authenticate(db, data.username, data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario o contraseña incorrectos",
        )

    token, expires_in = create_access_token(
        subject=user.Username,
        extra_claims={"role": user.Role, "uid": user.Id, "name": user.FullName},
    )

    user_service.update_last_login(db, user)

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=expires_in,
        user=UserResponse.model_validate(user),
    )


@auth_router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    """Perfil del usuario autenticado."""
    return current_user


@auth_router.post("/change-password")
def change_my_password(
    data: PasswordChangeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """El propio usuario cambia su contraseña (requiere la actual)."""
    try:
        user_service.change_password(
            db, current_user, data.current_password, data.new_password
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"message": "Contraseña actualizada exitosamente"}


@auth_router.post("/logout")
def logout():
    """Logout stateless: el cliente borra el token. Endpoint informativo."""
    return {"message": "Sesión cerrada. Elimina el token en el cliente."}
