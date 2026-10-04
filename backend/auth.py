"""Small, file-backed demo authentication for CITADEL."""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field, field_validator
from pwdlib import PasswordHash

from utils.config import ACCESS_TOKEN_EXPIRE_MINUTES, AUTH_USERS_PATH, ENVIRONMENT, JWT_ALGORITHM, JWT_SECRET_KEY

router = APIRouter(prefix="/auth", tags=["authentication"])
logger = logging.getLogger(__name__)
password_hash = PasswordHash.recommended()
_DUMMY_PASSWORD_HASH = password_hash.hash("citadel-dummy-password")
bearer = HTTPBearer(auto_error=False)
_lock = threading.RLock()
_MIN_SECRET_LENGTH = 32
_failed_logins: dict[str, tuple[int, float]] = {}
_RATE_WINDOW_SECONDS = 900
_MAX_FAILED_LOGINS = 10


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name is required.")
        return value

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()

    @field_validator("password")
    @classmethod
    def reasonable_password(cls, value: str) -> str:
        if not (re.search(r"[a-z]", value) and re.search(r"[A-Z]", value) and re.search(r"\d", value)):
            raise ValueError("Password must include uppercase, lowercase, and a number.")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()


class PublicUser(BaseModel):
    id: str
    name: str
    email: str
    created_at: str
    updated_at: str
    is_active: bool


def _load_users() -> list[dict[str, Any]]:
    path = Path(AUTH_USERS_PATH)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        logger.exception("Could not read the CITADEL auth user store.")
        raise HTTPException(status_code=503, detail="Authentication storage is unavailable.") from exc
    if not raw.strip():
        return []
    try:
        value = json.loads(raw)
        if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
            raise ValueError("Invalid user store format")
        return value
    except (json.JSONDecodeError, ValueError) as exc:
        logger.exception("The CITADEL auth user store contains invalid JSON or an invalid structure.")
        raise HTTPException(status_code=503, detail="Authentication storage is unavailable.") from exc


def _save_users(users: list[dict[str, Any]]) -> None:
    path = Path(AUTH_USERS_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False) as temp:
            temp_path = temp.name
            json.dump(users, temp, ensure_ascii=False, separators=(",", ":"))
            temp.flush()
            os.fsync(temp.fileno())
        if os.name != "nt":
            os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
    except OSError as exc:
        logger.exception("Could not atomically write the CITADEL auth user store.")
        raise HTTPException(status_code=503, detail="Authentication storage is unavailable.") from exc
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                logger.exception("Could not remove a temporary CITADEL auth store file.")


def _check_secret() -> str:
    secret = JWT_SECRET_KEY
    if len(secret) < _MIN_SECRET_LENGTH or secret.lower() in {"change-me", "change-me-in-production", "secret"}:
        logger.error(
            "Authentication configuration rejected: JWT_SECRET_KEY is missing, too short, or a placeholder (environment=%s).",
            ENVIRONMENT or "unset",
        )
        raise HTTPException(status_code=503, detail="Authentication is not configured.")
    if JWT_ALGORITHM != "HS256":
        logger.error("Authentication configuration rejected: JWT_ALGORITHM must be HS256.")
        raise HTTPException(status_code=503, detail="Authentication is not configured.")
    if ACCESS_TOKEN_EXPIRE_MINUTES < 1 or ACCESS_TOKEN_EXPIRE_MINUTES > 1440:
        logger.error("Authentication configuration rejected: ACCESS_TOKEN_EXPIRE_MINUTES is outside the allowed range.")
        raise HTTPException(status_code=503, detail="Authentication is not configured.")
    return secret


def validate_auth_configuration() -> None:
    """Fail application startup on invalid production auth configuration."""
    try:
        _check_secret()
    except HTTPException as exc:
        environment = ENVIRONMENT.strip().lower()
        if environment in {"prod", "production"}:
            raise RuntimeError(
                "Invalid authentication configuration: production requires a valid server-side JWT_SECRET_KEY."
            ) from exc


def _issue_token(user: dict[str, Any]) -> str:
    secret = _check_secret()
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": user["id"], "email": user["email"], "iat": now, "exp": now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)},
        secret,
        algorithm="HS256",
    )


def _find_user(user_id: str) -> dict[str, Any] | None:
    with _lock:
        return next((row for row in _load_users() if row.get("id") == user_id), None)


def resolve_token(token: str) -> dict[str, Any] | None:
    try:
        payload = jwt.decode(token, _check_secret(), algorithms=["HS256"], options={"require": ["sub", "exp"]})
        user = _find_user(str(payload["sub"]))
        return user if user and user.get("is_active") is True else None
    except (jwt.PyJWTError, HTTPException, KeyError, TypeError, ValueError):
        return None


def public_user(user: dict[str, Any]) -> PublicUser:
    return PublicUser.model_validate({key: user[key] for key in PublicUser.model_fields})


async def get_current_user(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict[str, Any]:
    user = getattr(request.state, "current_user", None)
    if user is None and credentials is not None:
        user = resolve_token(credentials.credentials)
    if user is None or user.get("is_active") is not True:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.", headers={"WWW-Authenticate": "Bearer"})
    return user


@router.post("/register", response_model=PublicUser, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest) -> PublicUser:
    try:
        _check_secret()
        now = datetime.now(timezone.utc).isoformat()
        with _lock:
            users = _load_users()
            if any(row.get("email") == payload.email for row in users):
                raise HTTPException(status_code=409, detail="An account with this email already exists.")
            user = {
                "id": str(uuid.uuid4()), "name": payload.name, "email": payload.email,
                "password_hash": password_hash.hash(payload.password), "created_at": now,
                "updated_at": now, "is_active": True,
            }
            users.append(user)
            _save_users(users)
        return public_user(user)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Unexpected error while registering a CITADEL user.")
        raise HTTPException(status_code=500, detail="Registration could not be completed.") from exc


@router.post("/login")
def login(payload: LoginRequest, request: Request) -> dict[str, Any]:
    _check_secret()
    client_ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    with _lock:
        if len(_failed_logins) >= 4096:
            for expired_ip in [ip for ip, (_, started) in _failed_logins.items() if now - started >= _RATE_WINDOW_SECONDS]:
                _failed_logins.pop(expired_ip, None)
        attempts, window_start = _failed_logins.get(client_ip, (0, now))
        if now - window_start >= _RATE_WINDOW_SECONDS:
            attempts, window_start = 0, now
        if attempts >= _MAX_FAILED_LOGINS:
            raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")
        if len(_failed_logins) >= 4096 and client_ip not in _failed_logins:
            _failed_logins.pop(next(iter(_failed_logins)))
        _failed_logins[client_ip] = (attempts + 1, window_start)
    with _lock:
        user = next((row for row in _load_users() if row.get("email") == payload.email), None)
    # Perform a hash operation for unknown emails too, reducing account-enumeration timing signals.
    valid = password_hash.verify(payload.password, user["password_hash"] if user else _DUMMY_PASSWORD_HASH)
    if not user or not valid or user.get("is_active") is not True:
        raise HTTPException(status_code=401, detail="Invalid email or password.", headers={"WWW-Authenticate": "Bearer"})
    with _lock:
        _failed_logins.pop(client_ip, None)
    return {"access_token": _issue_token(user), "token_type": "bearer", "user": public_user(user)}


@router.get("/me", response_model=PublicUser)
def current_user(user: dict[str, Any] = Depends(get_current_user)) -> PublicUser:
    return public_user(user)
