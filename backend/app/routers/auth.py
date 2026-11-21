from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import Membership, Organization, User
from ..schemas import LoginIn, PreferencesIn, RegisterIn, UserOut
from ..security import (
    audit,
    clear_session_cookie,
    client_ip,
    create_session_token,
    enforce_rate_limit,
    get_current_user,
    hash_password,
    primary_org_id,
    set_session_cookie,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Verified against when the email is unknown so response time doesn't reveal which emails exist.
_DUMMY_HASH = hash_password("timing-equalizer-not-a-real-password-1")


@router.post("/register", response_model=UserOut, status_code=201)
def register(body: RegisterIn, request: Request, response: Response, db: Session = Depends(get_db)):
    enforce_rate_limit(f"auth:{client_ip(request)}", get_settings().rate_limit_auth_per_minute)
    email = body.email.lower()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    user = User(email=email, password_hash=hash_password(body.password), display_name=body.display_name.strip(),
                preferences={})
    org = Organization(name=f"{user.display_name}'s workspace")
    db.add_all([user, org])
    db.flush()
    db.add(Membership(user_id=user.id, org_id=org.id, role="owner"))
    audit(db, "auth.register", user_id=user.id, org_id=org.id, ip=client_ip(request))
    db.commit()
    set_session_cookie(response, create_session_token(user))
    return user


@router.post("/login", response_model=UserOut)
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    enforce_rate_limit(f"auth:{client_ip(request)}", get_settings().rate_limit_auth_per_minute)
    user = db.query(User).filter(User.email == body.email.lower()).first()
    ok = verify_password(user.password_hash if user else _DUMMY_HASH, body.password)
    if not user or not ok or not user.is_active:
        audit(db, "auth.login_failed", ip=client_ip(request), details={"email_domain": body.email.split("@")[-1]})
        db.commit()
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    audit(db, "auth.login", user_id=user.id, ip=client_ip(request))
    db.commit()
    set_session_cookie(response, create_session_token(user))
    return user


@router.post("/logout", status_code=204)
def logout(response: Response):
    clear_session_cookie(response)
    response.status_code = 204
    return response


@router.post("/logout-all", status_code=204)
def logout_all(request: Request, response: Response, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    """Revoke every outstanding session for this user."""
    user.token_version += 1
    audit(db, "auth.logout_all", user_id=user.id, ip=client_ip(request))
    db.commit()
    clear_session_cookie(response)
    response.status_code = 204
    return response


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.patch("/me/preferences", response_model=UserOut)
def update_preferences(body: PreferencesIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    prefs = dict(user.preferences or {})
    prefs.update(body.model_dump(exclude_none=True))
    user.preferences = prefs
    audit(db, "user.preferences_updated", user_id=user.id, org_id=primary_org_id(db, user))
    db.commit()
    return user
