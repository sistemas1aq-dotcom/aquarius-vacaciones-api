"""Dependencies de FastAPI para autenticación/autorización."""
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import User
from .security import decode_token


# OAuth2 bearer: no auto-error; nosotros manejamos el error para dar mensajes claros.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Resuelve el usuario actual a partir del JWT."""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de autenticación requerido",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )

    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=401, detail="Token sin subject")

    user = db.query(User).filter(User.Username == username).first()
    if not user:
        raise HTTPException(status_code=401, detail="Usuario no encontrado")
    if not user.IsActive:
        raise HTTPException(status_code=403, detail="Usuario desactivado")

    return user


def require_auth(current_user: User = Depends(get_current_user)) -> User:
    """Alias explícito para rutas que solo requieren estar autenticado."""
    return current_user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Solo rol admin."""
    if current_user.Role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Se requiere rol administrador",
        )
    return current_user
