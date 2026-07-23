import hashlib
import secrets
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from . import config, models, services
from .database import get_db

SECRET_KEY = config.get("SECRET_KEY", "degistir-bu-anahtari-uretimde")
ALGORITHM = "HS256"
# Faz 23 — Güvenlik sertleştirme: erişim jetonu kısa ömürlü (2 saat), oturum
# uzun süre kesintisiz kalabilsin diye REFRESH_TOKEN_EXPIRE_DAYS ile
# (aşağıda) uzun ömürlü, iptal edilebilir bir yenileme jetonu eşlik eder.
# Önceki sürümde 12 saatlik tek bir jeton vardı; artık ön yüz periyodik
# olarak sessizce yeniliyor (bkz. frontend App.tsx), bu yüzden kısaltmak
# kullanıcı deneyimini bozmadan güvenliği artırır (çalınan bir jeton en
# fazla 2 saat geçerli olur).
TOKEN_EXPIRE_MINUTES = 60 * 2
REFRESH_TOKEN_EXPIRE_DAYS = 30

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def create_token(user: models.User) -> str:
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "exp": datetime.utcnow() + timedelta(minutes=TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def _hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def issue_refresh_token(db: Session, user: models.User, ip: str = "", user_agent: str = "") -> str:
    """Yeni bir yenileme jetonu üretir, özetini (hash) veritabanına kaydeder
    ve HAM jetonu döner (yalnızca bu anda görülür, saklanan yalnızca özet)."""
    raw = secrets.token_urlsafe(48)
    row = models.RefreshToken(
        user_id=user.id,
        token_hash=_hash_refresh_token(raw),
        expires_at=datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        ip_address=ip[:64],
        user_agent=user_agent[:300],
    )
    db.add(row)
    db.commit()
    return raw


def rotate_refresh_token(
    db: Session, raw_token: str, ip: str = "", user_agent: str = ""
) -> tuple[str, models.User] | None:
    """Verilen yenileme jetonu geçerliyse (bulunuyor, iptal edilmemiş, süresi
    dolmamış ve sahibi hâlâ aktifse): jetonu iptal eder (rotation — tek
    kullanımlık) ve (yeni_jeton, kullanıcı) döner. Geçersizse None döner."""
    row = (
        db.query(models.RefreshToken)
        .filter_by(token_hash=_hash_refresh_token(raw_token))
        .first()
    )
    if not row or row.revoked_at is not None or row.expires_at < datetime.utcnow():
        return None
    user = db.get(models.User, row.user_id)
    if not user or not user.is_active:
        return None
    row.revoked_at = datetime.utcnow()
    db.commit()
    return issue_refresh_token(db, user, ip, user_agent), user


def revoke_refresh_token(db: Session, raw_token: str) -> None:
    row = (
        db.query(models.RefreshToken)
        .filter_by(token_hash=_hash_refresh_token(raw_token))
        .first()
    )
    if row and row.revoked_at is None:
        row.revoked_at = datetime.utcnow()
        db.commit()


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> models.User:
    cred_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Oturum geçersiz"
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub"))
    except (JWTError, TypeError, ValueError):
        raise cred_error
    user = db.get(models.User, user_id)
    if not user or not user.is_active:
        raise cred_error
    return user

def require_roles(*roles: str):
    def dep(user: models.User = Depends(get_current_user)) -> models.User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Bu işlem için yetkiniz yok")
        return user

    return dep

require_admin = require_roles("admin")
# "amir" (saha amiri/denetçi) mühendisle aynı denetim seviyesinde sayılır.
require_staff = require_roles("admin", "engineer", "amir")

def require_permission(permission: str):
    def dep(
        user: models.User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> models.User:
        if not services.has_permission(db, user, permission):
            raise HTTPException(status_code=403, detail="Bu işlem için yetkiniz yok")
        return user

    return dep
