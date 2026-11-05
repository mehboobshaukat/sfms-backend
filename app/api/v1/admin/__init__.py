"""
Admin layer: everything under /admin/*.

The guard is attached ONCE, at router level, so an endpoint added here later cannot
forget it. Routes that only the super admin may use live in admins.py, which adds
require_super_admin on top.
"""
from fastapi import APIRouter, Depends

from app.api.v1.admin import admins, me, messages, users
from app.core.deps import require_admin

router = APIRouter(prefix="/admin", tags=["Admin"], dependencies=[Depends(require_admin)])
router.include_router(me.router)
router.include_router(messages.router)
router.include_router(users.router)
router.include_router(admins.router)
