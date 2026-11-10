from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.core.database import get_db
from app.core.config import settings
from app.core.limiter import limiter
from app.crud import auth_service
from app.schemas.auth import (
    RegisterRequest,
    LoginRequest,
    TokenResponse,
    RefreshRequest,
    LogoutRequest,
    VerifyEmailRequest,
    ResendVerificationRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    UserOut,
)
from app.utils.profile_images import delete_profile_image, save_profile_image

router = APIRouter(prefix="/auth", tags=["Auth"])


async def _parse_register_request(request: Request) -> tuple[RegisterRequest, StarletteUploadFile | None]:
    """Keep the existing JSON registration contract and add multipart support.

    Existing clients can continue POSTing JSON. A frontend that wants to use
    the new optional profile image can POST multipart/form-data with the same
    text fields plus a `profile_image` file.
    """
    content_type = request.headers.get("content-type", "").lower()
    profile_image: StarletteUploadFile | None = None

    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        profile_candidate = form.get("profile_image")
        if isinstance(profile_candidate, StarletteUploadFile) and profile_candidate.filename:
            profile_image = profile_candidate

        raw = {
            "username": form.get("username"),
            "first_name": form.get("first_name"),
            "last_name": form.get("last_name"),
            "email": form.get("email"),
            "phone_number": form.get("phone_number") or None,
            "country": form.get("country"),
            "state": form.get("state"),
            "city": form.get("city") or None,
            "postal_code": form.get("postal_code") or None,
            "password": form.get("password"),
        }
    else:
        try:
            raw = await request.json()
        except Exception as exc:
            raise RequestValidationError([{
                "type": "json_invalid",
                "loc": ("body",),
                "msg": "Request body must be valid JSON or multipart/form-data.",
                "input": None,
            }]) from exc

    try:
        return RegisterRequest.model_validate(raw), profile_image
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def register(request: Request, db: Session = Depends(get_db)):
    """
    Creates the user, seeds their default accounts and requested default
    transaction categories, and sends a required email-verification message.

    Backward compatible: JSON is still accepted. For optional profile-image
    upload, use multipart/form-data and add the `profile_image` field.
    """
    payload, profile_image = await _parse_register_request(request)
    saved_profile_image_url: str | None = None

    try:
        if profile_image is not None and settings.PROFILE_IMAGE_ENABLED:
            saved_profile_image_url = await save_profile_image(profile_image)

        user = auth_service.register_user(
            db,
            username=payload.username,
            first_name=payload.first_name,
            last_name=payload.last_name,
            email=str(payload.email),
            password=payload.password,
            phone_number=payload.phone_number,
            country=payload.country,
            state=payload.state,
            city=payload.city,
            postal_code=payload.postal_code,
            profile_image_url=saved_profile_image_url,
        )
        return user
    except Exception:
        # The DB registration is transactional. If it fails (including SMTP
        # failure), remove a just-uploaded image so no orphan file is left.
        if saved_profile_image_url:
            delete_profile_image(saved_profile_image_url)
        raise


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
def login(request: Request, payload: LoginRequest, db: Session = Depends(get_db)):
    access_token, refresh_token = auth_service.login_user(db, str(payload.email), payload.password)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh-token", response_model=TokenResponse)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)):
    access_token, refresh_token = auth_service.refresh_tokens(db, payload.refresh_token)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(payload: LogoutRequest, db: Session = Depends(get_db)):
    auth_service.logout_user(db, payload.refresh_token)


@router.post("/verify-email", response_model=UserOut)
@limiter.limit("10/minute")
def verify_email(request: Request, payload: VerifyEmailRequest, db: Session = Depends(get_db)):
    """Verify one opaque, single-use email-verification token."""
    return auth_service.verify_email(db, payload.token)


@router.get("/verify-email")
@limiter.limit("10/minute")
def verify_email_from_link(request: Request, token: str):
    """Backward-compatible target for verification links issued by older builds.

    Verification is deliberately completed with the POST API, not a state-
    changing GET. Old backend links are therefore redirected into the same
    frontend verification page used by new emails, which also gives expired or
    invalid tokens a proper user-facing error state.
    """
    base = settings.PUBLIC_FRONTEND_URL.rstrip("/")
    target = f"{base}/verify-email?{urlencode({'token': token})}"
    return RedirectResponse(
        url=target,
        status_code=status.HTTP_303_SEE_OTHER,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@router.post("/resend-verification", status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("3/minute")
def resend_verification(request: Request, payload: ResendVerificationRequest, db: Session = Depends(get_db)):
    auth_service.resend_verification_email(db, str(payload.email))
    return {"detail": "If that unverified account exists, a new verification email has been sent."}


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("3/minute")
def forgot_password(request: Request, payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """
    Always returns 202 regardless of whether the email exists (prevents
    account enumeration). Existing password-reset behavior is unchanged.
    """
    auth_service.request_password_reset(db, str(payload.email))
    return {"detail": "If that email exists, a reset link has been sent."}


@router.post("/reset-password", response_model=UserOut)
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    return auth_service.reset_password(db, payload.token, payload.new_password)
