from sqlalchemy import String, Boolean, Integer, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import TimestampMixin, SlugMixin


class Priority(Base, TimestampMixin, SlugMixin):
    __tablename__ = "priorities"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_priority_user_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)  # NULL = system default
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
