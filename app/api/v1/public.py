"""Unauthenticated endpoints."""
from fastapi import APIRouter, Depends, Request, status
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.limiter import limiter
from app.crud import contact_message_service
from app.schemas.admin import ContactSubmit

router = APIRouter(prefix="/public", tags=["Public"])

_THANKS = {"detail": "Thanks - your message has been received."}


@router.post("/contact", status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("3/hour")
def submit_contact_form(request: Request, payload: ContactSubmit, db: Session = Depends(get_db)):
    """Public contact form. Always answers the same generic 202 so bots learn nothing."""
    if payload.website.strip():  # honeypot filled in -> bot; pretend success, store nothing
        return _THANKS
    contact_message_service.create_submission(db, payload, get_remote_address(request))
    return _THANKS
