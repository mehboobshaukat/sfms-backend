"""
Import every model here so that:
  1. `Base.metadata.create_all()` (dev convenience) sees all tables.
  2. Alembic's autogenerate can detect all models.
  3. SQLAlchemy can resolve string-based relationship() references.
"""
from app.models.user import User
from app.models.refresh_token import RefreshToken
from app.models.transaction_type import TransactionType
from app.models.category import Category
from app.models.group import Group
from app.models.contact import Contact
from app.models.links import CategoryTransactionType, CategoryGroup, GroupContact
from app.models.account import Account
from app.models.payment_method import PaymentMethod
from app.models.priority import Priority
from app.models.loan import Loan
from app.models.credit_purchase import CreditPurchase
from app.models.transaction import Transaction
from app.models.transaction_draft import TransactionDraft
from app.models.ai_bulk_session import AiBulkSession
from app.models.history_entry import HistoryEntry
from app.models.budget import Budget
from app.models.goal import Goal
from app.models.goal_allocation import GoalAllocation
from app.models.contact_message import ContactMessage, ContactMessageReply
from app.models.admin_action import AdminAction

__all__ = [
    "User",
    "RefreshToken",
    "TransactionType",
    "Category",
    "Group",
    "Contact",
    "CategoryTransactionType",
    "CategoryGroup",
    "GroupContact",
    "Account",
    "PaymentMethod",
    "Priority",
    "Loan",
    "CreditPurchase",
    "Transaction",
    "TransactionDraft",
    "AiBulkSession",
    "HistoryEntry",
    "Budget",
    "Goal",
    "GoalAllocation",
    "ContactMessage",
    "ContactMessageReply",
    "AdminAction",
]
