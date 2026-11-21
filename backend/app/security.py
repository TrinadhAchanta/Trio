"""Password hashing, session tokens, the current-user dependency, rate limiting, and audit."""

import time
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from threading import Lock

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import AuditEvent, Membership, User

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def create_session_token(user: User) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user.id),
        "ver": user.token_version,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=s.session_ttl_hours)).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def set_session_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        s.cookie_name,
        token,
        max_age=s.session_ttl_hours * 3600,
        httponly=True,
        secure=s.cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    s = get_settings()
    response.delete_cookie(s.cookie_name, path="/")


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in")


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    s = get_settings()
    token = request.cookies.get(s.cookie_name)
    if not token:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:]
    if not token:
        raise _unauthorized()
    try:
        payload = jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
        user_id = uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise _unauthorized() from None
    user = db.get(User, user_id)
    if not user or not user.is_active or payload.get("ver") != user.token_version:
        raise _unauthorized()
    return user


def primary_org_id(db: Session, user: User) -> uuid.UUID:
    m = db.query(Membership).filter(Membership.user_id == user.id).order_by(Membership.created_at).first()
    if not m:
        raise HTTPException(status_code=403, detail="User has no organization")
    return m.org_id


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def audit(
    db: Session,
    action: str,
    *,
    user_id: uuid.UUID | None = None,
    org_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    ip: str | None = None,
    details: dict | None = None,
) -> None:
    db.add(
        AuditEvent(
            user_id=user_id,
            org_id=org_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            ip=ip,
            details=details or {},
        )
    )


class SlidingWindowLimiter:
    """In-process sliding-window limiter.

    Good enough for a single API instance. For multiple instances swap this for a Redis-backed
    implementation with the same ``hit`` interface (tracked in PROGRESS.md).
    """

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def hit(self, key: str, limit: int, window_s: float = 60.0) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_s:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = SlidingWindowLimiter()


def enforce_rate_limit(key: str, limit: int) -> None:
    if not limiter.hit(key, limit):
        raise HTTPException(status_code=429, detail="Too many requests. Please wait a moment and try again.")
