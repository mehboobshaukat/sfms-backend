from sqlalchemy import String, Boolean, Integer, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, SlugMixin


class PaymentMethod(Base, TimestampMixin, SlugMixin):
    __tablename__ = "payment_methods"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_payment_method_user_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    account = relationship("Account", back_populates="payment_methods")

    @property
    def account_label(self) -> str | None:
        return self.account.name if self.account else None

    @property
    def account_slug(self) -> str | None:
        return self.account.slug if self.account else None
