from fastapi import APIRouter

from app.core.config import settings
from app.api.v1 import public
from app.api.v1.admin import router as admin_router
from app.api.v1.endpoints import (
    auth,
    users,
    transaction_types,
    categories,
    groups,
    contacts,
    accounts,
    payment_methods,
    priorities,
    transactions,
    loans,
    credit_purchases,
    transaction_drafts,
    history,
    budgets,
    goals,
    analytics,
    locations,
    ai_bulk,
)

api_router = APIRouter()

# ---- Core finance layer (always mounted) ----
api_router.include_router(locations.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(transaction_types.router)
api_router.include_router(categories.router)
api_router.include_router(groups.router)
api_router.include_router(contacts.router)
api_router.include_router(accounts.router)
api_router.include_router(payment_methods.router)
api_router.include_router(priorities.router)
api_router.include_router(transactions.router)
api_router.include_router(loans.router)
api_router.include_router(credit_purchases.router)
api_router.include_router(transaction_drafts.router)
api_router.include_router(history.router)
api_router.include_router(budgets.router)
api_router.include_router(goals.router)
api_router.include_router(analytics.router)
api_router.include_router(public.router)

# ---- Admin layer (own router + own guard). Switch off with ADMIN_PANEL_ENABLED=false. ----
if settings.ADMIN_PANEL_ENABLED:
    api_router.include_router(admin_router)

# ---- AI layer (optional). When AI_ENABLED=false, core stays healthy. ----
if settings.AI_ENABLED:
    api_router.include_router(ai_bulk.router)
