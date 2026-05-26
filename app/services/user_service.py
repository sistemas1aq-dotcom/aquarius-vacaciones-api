"""CRUD y lógica de negocio de usuarios."""
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session

from app.models.models import User
from app.schemas.schemas import UserCreate, UserUpdate
from app.auth.security import hash_password, verify_password


VALID_ROLES = {"admin", "gestor", "trabajador"}
# admin     -> Administrador de Sistema (acceso total, gestion de usuarios)
# gestor    -> Gestor de RRHH (gestion de empleados, vacaciones, recordatorios)
# trabajador-> Trabajador (vista de su propia info)


def _validate_role(role: str):
    if role not in VALID_ROLES:
        raise ValueError(f"Rol inválido '{role}'. Debe ser uno de: {VALID_ROLES}")


def get_by_username(db: Session, username: str) -> Optional[User]:
    return db.query(User).filter(User.Username == username).first()


def get_by_id(db: Session, user_id: int) -> Optional[User]:
    return db.query(User).filter(User.Id == user_id).first()


def list_users(
    db: Session, is_active: Optional[bool] = None,
    role: Optional[str] = None, search: Optional[str] = None,
) -> list[User]:
    query = db.query(User)
    if is_active is not None:
        query = query.filter(User.IsActive == is_active)
    if role:
        query = query.filter(User.Role == role)
    if search:
        term = f"%{search}%"
        query = query.filter(
            (User.Username.ilike(term))
            | (User.FullName.ilike(term))
            | (User.Email.ilike(term))
        )
    return query.order_by(User.FullName).all()


def create_user(db: Session, data: UserCreate) -> User:
    _validate_role(data.Role)

    # Verificar unicidad
    if get_by_username(db, data.Username):
        raise ValueError(f"El username '{data.Username}' ya existe")
    if db.query(User).filter(User.Email == data.Email).first():
        raise ValueError(f"El email '{data.Email}' ya está registrado")

    user = User(
        Username=data.Username.strip(),
        Email=str(data.Email).strip().lower(),
        FullName=data.FullName.strip(),
        PasswordHash=hash_password(data.Password),
        Role=data.Role,
        IsActive=data.IsActive,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def update_user(db: Session, user_id: int, data: UserUpdate) -> Optional[User]:
    user = get_by_id(db, user_id)
    if not user:
        return None

    update_data = data.model_dump(exclude_unset=True)
    if "Role" in update_data and update_data["Role"]:
        _validate_role(update_data["Role"])

    for key, value in update_data.items():
        if key == "Email" and value:
            # Verificar que no exista otro usuario con ese email
            existing = db.query(User).filter(
                User.Email == str(value).lower(),
                User.Id != user_id,
            ).first()
            if existing:
                raise ValueError("El email ya está registrado por otro usuario")
            setattr(user, key, str(value).lower())
        else:
            setattr(user, key, value)

    db.commit()
    db.refresh(user)
    return user


def change_password(
    db: Session, user: User, current_password: str, new_password: str
) -> bool:
    """El propio usuario cambia su password (requiere la password actual)."""
    if not verify_password(current_password, user.PasswordHash):
        raise ValueError("La contraseña actual es incorrecta")
    if len(new_password) < 8:
        raise ValueError("La nueva contraseña debe tener al menos 8 caracteres")
    user.PasswordHash = hash_password(new_password)
    db.commit()
    return True


def reset_password(db: Session, user_id: int, new_password: str) -> Optional[User]:
    """Admin resetea la password de cualquier usuario."""
    user = get_by_id(db, user_id)
    if not user:
        return None
    if len(new_password) < 8:
        raise ValueError("La contraseña debe tener al menos 8 caracteres")
    user.PasswordHash = hash_password(new_password)
    db.commit()
    db.refresh(user)
    return user


def set_active(db: Session, user_id: int, is_active: bool) -> Optional[User]:
    user = get_by_id(db, user_id)
    if not user:
        return None
    user.IsActive = is_active
    db.commit()
    db.refresh(user)
    return user


def update_last_login(db: Session, user: User):
    user.LastLoginAt = datetime.utcnow()
    db.commit()


def authenticate(db: Session, username: str, password: str) -> Optional[User]:
    """Autentica por username (o email) + password."""
    user = get_by_username(db, username)
    if not user:
        # Permitir login por email también
        user = db.query(User).filter(User.Email == username.lower()).first()
    if not user:
        return None
    if not user.IsActive:
        return None
    if not verify_password(password, user.PasswordHash):
        return None
    return user
