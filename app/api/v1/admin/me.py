from fastapi import APIRouter, Depends

from app.core.deps import require_admin
from app.core.roles import ROLE_SUPER_ADMIN
from app.models.user import User
from app.schemas.admin import AdminMeOut

router = APIRouter()


@router.get("/me", response_model=AdminMeOut)
def admin_me(current_user: User = Depends(require_admin)):
    """Who am I and what may I do - the panel uses this to show/hide controls
    (the backend still enforces every rule on its own)."""
    is_super = current_user.role == ROLE_SUPER_ADMIN
    return {
        "username": current_user.username, "email": current_user.email,
        "first_name": current_user.first_name, "last_name": current_user.last_name,
        "role": current_user.role,
        "capabilities": {
            "manage_messages": True,
            "manage_users": True,
            "block_admins": is_super,
            "manage_admins": is_super,
        },
    }
