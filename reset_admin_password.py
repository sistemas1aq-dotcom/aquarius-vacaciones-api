"""
Resetea la contrasena del usuario 'admin' a 'Admin123!' en la base local.
Uso:
    cd backend
    .\venv\Scripts\Activate.ps1
    python reset_admin_password.py
"""
from app.database import SessionLocal
from app.models.models import User
from app.auth.security import hash_password

NEW_USERNAME = "admin"
NEW_PASSWORD = "Admin123!"

def main():
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.Username == NEW_USERNAME).first()
        if not user:
            print(f"[ERROR] No existe el usuario '{NEW_USERNAME}'.")
            print("Usuarios existentes:")
            for u in db.query(User).all():
                print(f"  - {u.Username}  ({u.FullName})  role={u.Role}  active={u.IsActive}")
            return

        user.PasswordHash = hash_password(NEW_PASSWORD)
        if hasattr(user, "IsActive"):
            user.IsActive = True
        db.commit()
        print(f"[OK] Password actualizada para '{user.Username}' -> {user.FullName}")
        print(f"     Nueva pass: {NEW_PASSWORD}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
