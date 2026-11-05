from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, get_current_user_allow_pending_deletion
from app.crud import auth_service
from app.models.user import User
from app.models.account import Account
from app.models.transaction import Transaction
from app.models.budget import Budget
from app.models.goal import Goal
from app.models.loan import Loan
from app.models.credit_purchase import CreditPurchase
from app.utils.geography import country_metadata
from app.schemas.auth import UserOut, UserUpdateRequest, ChangePasswordRequest, DeleteAccountRequest
from app.utils.profile_images import delete_profile_image, save_profile_image

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("/me", response_model=UserOut)
def get_me(current_user: User = Depends(get_current_user_allow_pending_deletion)):
    """
    Uses the permissive dependency (not get_current_user) so a user whose
    account is scheduled for deletion can still fetch their own status —
    including is_deleted / deletion_scheduled_at — to render a "cancel
    deletion?" banner client-side.
    """
    return current_user


@router.put("/me", response_model=UserOut)
def update_me(payload: UserUpdateRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Keep username uniqueness guaranteed when a user changes it later too.
    if payload.username is not None and payload.username != current_user.username:
        duplicate = db.query(User).filter(
            func.lower(User.username) == payload.username.lower(),
            User.id != current_user.id,
        ).first()
        if duplicate:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already taken.")
        current_user.username = payload.username

    if payload.first_name is not None:
        current_user.first_name = payload.first_name
    if payload.last_name is not None:
        current_user.last_name = payload.last_name
    if "phone_number" in payload.model_fields_set:
        current_user.phone_number = payload.phone_number

    if "country" in payload.model_fields_set:
        if payload.country is None:
            raise HTTPException(422, "Country cannot be cleared. Select a valid country.")
        # Do not silently relabel existing PKR amounts as USD, EUR, etc.
        # Existing accounts without country are treated as legacy PKR.
        new_currency = country_metadata(payload.country)["currency_code"]
        if new_currency != current_user.currency_code:
            financial_records_exist = any((
                db.query(Transaction.id).filter(Transaction.user_id == current_user.id).first(),
                db.query(Budget.id).filter(Budget.user_id == current_user.id).first(),
                db.query(Goal.id).filter(Goal.user_id == current_user.id).first(),
                db.query(Loan.id).filter(Loan.user_id == current_user.id).first(),
                db.query(CreditPurchase.id).filter(CreditPurchase.user_id == current_user.id).first(),
                db.query(Account.id).filter(
                    Account.user_id == current_user.id,
                    Account.current_balance != 0,
                ).first(),
            ))
            if financial_records_exist:
                raise HTTPException(
                    409,
                    "Cannot switch base currency while financial records exist. "
                    "Existing amounts are not converted; an FX migration is required.",
                )
        # Prevent stale province from an earlier country.
        if payload.country != current_user.country:
            if "state" not in payload.model_fields_set or not payload.state:
                raise HTTPException(422, "Provide a state/province when changing country.")
        current_user.country = payload.country
    if "state" in payload.model_fields_set:
        if not payload.state:
            raise HTTPException(422, "State/province cannot be empty.")
        current_user.state = payload.state
    if "city" in payload.model_fields_set:
        current_user.city = payload.city
    if "postal_code" in payload.model_fields_set:
        current_user.postal_code = payload.postal_code

    db.commit()
    db.refresh(current_user)
    return current_user


@router.post("/me/profile-image", response_model=UserOut)
async def upload_profile_image(
    profile_image: UploadFile,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Optional profile image; JPEG/PNG/WebP, limited by MAX_PROFILE_IMAGE_MB."""
    new_url = await save_profile_image(profile_image)
    old_url = current_user.profile_image_url
    try:
        current_user.profile_image_url = new_url
        db.commit()
        db.refresh(current_user)
    except Exception:
        db.rollback()
        delete_profile_image(new_url)
        raise

    if old_url and old_url != new_url:
        delete_profile_image(old_url)
    return current_user


@router.delete("/me/profile-image", status_code=status.HTTP_204_NO_CONTENT)
def remove_profile_image(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    old_url = current_user.profile_image_url
    current_user.profile_image_url = None
    db.commit()
    delete_profile_image(old_url)


@router.put("/me/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    auth_service.change_password(db, current_user, payload.current_password, payload.new_password)


@router.delete("/me", status_code=status.HTTP_202_ACCEPTED)
def delete_me(
    payload: DeleteAccountRequest = Body(default_factory=DeleteAccountRequest),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Two ways to close the account, chosen by the user at delete time:

    - `deletion_type: "scheduled"` (default) — 30-day recovery grace
      period. The session (and its refresh token) stays valid for the
      full 30 days specifically so the account can reach
      POST /users/me/cancel-deletion at any point in that window — see
      get_current_user_allow_pending_deletion and the matching carve-out
      in auth_service.refresh_tokens().
    - `deletion_type: "instant"` — permanently erases the account and
      every row it owns immediately. Cannot be undone.
    """
    if current_user.role != "user":
        # An admin / the super admin vanishing (even on a 30-day timer) would orphan the panel.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin accounts cannot be deleted. Ask the super admin to remove admin access first.",
        )
    if payload.deletion_type == "instant":
        user_id = current_user.id
        auth_service.hard_delete_user(db, user_id)
        return {"detail": "Account and all associated data permanently deleted."}

    scheduled_at = auth_service.schedule_deletion(db, current_user, days=30)
    return {
        "detail": "Account scheduled for deletion in 30 days. You can cancel any time before then.",
        "deletion_scheduled_at": scheduled_at,
    }


@router.post("/me/cancel-deletion", response_model=UserOut)
def cancel_deletion(
    current_user: User = Depends(get_current_user_allow_pending_deletion),
    db: Session = Depends(get_db),
):
    """
    Cancels a pending 30-day scheduled deletion. Deliberately uses
    get_current_user_allow_pending_deletion instead of get_current_user:
    the normal dependency rejects any request from an is_deleted account,
    which would make this endpoint permanently unreachable — a user could
    never cancel a deletion they're blocked from authenticating for.
    """
    auth_service.cancel_scheduled_deletion(db, current_user)
    db.refresh(current_user)
    return current_user
