"""
Join tables powering the Option-B "reusable, many-to-many" redesign of
Category / Group / Contact. Each link table is a pure association: no
extra fields beyond the two FKs and a timestamp, and a composite unique
constraint prevents the same pair being linked twice.

Deletion semantics (implemented in app/crud/deletion_guard.py): removing
one of these rows is an "unlink", not a delete of the underlying
Category/Group/Contact. The underlying row is only actually removed when
it has zero remaining links (and isn't otherwise referenced by a live
transaction) — see check_*_deletion() for the exact rules.
"""
from datetime import datetime, timezone

from sqlalchemy import Integer, ForeignKey, UniqueConstraint, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class CategoryTransactionType(Base):
    __tablename__ = "category_transaction_types"
    __table_args__ = (UniqueConstraint("category_id", "transaction_type_id", name="uq_category_transaction_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), nullable=False, index=True)
    transaction_type_id: Mapped[int] = mapped_column(ForeignKey("transaction_types.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    category = relationship("Category", back_populates="transaction_type_links")
    transaction_type = relationship("TransactionType", back_populates="category_links")


class CategoryGroup(Base):
    __tablename__ = "category_groups"
    __table_args__ = (UniqueConstraint("category_id", "group_id", name="uq_category_group"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), nullable=False, index=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    category = relationship("Category", back_populates="group_links")
    group = relationship("Group", back_populates="category_links")


class GroupContact(Base):
    __tablename__ = "group_contacts"
    __table_args__ = (UniqueConstraint("group_id", "contact_id", name="uq_group_contact"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), nullable=False, index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    group = relationship("Group", back_populates="contact_links")
    contact = relationship("Contact", back_populates="group_links")
