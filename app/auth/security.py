"""Hashing de passwords y emisión/validación de JWT."""
from datetime import datetime, timedelta, timezone
from typing import Optional
from passlib.context import CryptContext
from jose import jwt, JWTError

from app.config import get_settings

settings = get_settings()

# Contexto bcrypt (rounds=12 por defecto)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    """Genera un hash bcrypt de una password en texto plano."""
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Verifica si una password en texto plano corresponde al hash dado."""
    try:
        return pwd_context.verify(plain, hashed)
    except Exception:
        return False


def create_access_token(
    subject: str,
    extra_claims: Optional[dict] = None,
    expires_minutes: Optional[int] = None,
) -> tuple[str, int]:
    """Crea un JWT firmado con el secret configurado.

    Retorna (token, expires_in_seconds).
    """
    exp_minutes = expires_minutes or settings.jwt_expire_minutes
    expire_at = datetime.now(timezone.utc) + timedelta(minutes=exp_minutes)

    to_encode = {
        "sub": str(subject),
        "exp": expire_at,
        "iat": datetime.now(timezone.utc),
    }
    if extra_claims:
        to_encode.update(extra_claims)

    encoded = jwt.encode(
        to_encode,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return encoded, exp_minutes * 60


def decode_token(token: str) -> Optional[dict]:
    """Decodifica un JWT y retorna el payload o None si es inválido/expirado."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        return payload
    except JWTError:
        return None
