from sqlalchemy import String, Boolean, Integer, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, SlugMixin


class Group(Base, TimestampMixin, SlugMixin):
    """
    Standalone, reusable per-user entity (Option B) — not owned by a
    single Category. Which categories it's linked under is tracked via
    CategoryGroup; which contacts belong to it is tracked via
    GroupContact. In the transaction form, Group selection is OPTIONAL:
    picking a Category alone auto-loads contacts from every group linked
    to it (union across groups), so Group only matters when the user
    wants to narrow that list further.
    """
    __tablename__ = "groups"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_group_user_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    category_links = relationship("CategoryGroup", back_populates="group", cascade="all, delete-orphan")
    contact_links = relationship("GroupContact", back_populates="group", cascade="all, delete-orphan")

    @property
    def category_labels(self) -> dict[str, str]:
        return {link.category.slug: link.category.name for link in self.category_links}

    @property
    def category_slugs(self) -> list[str]:
        return [link.category.slug for link in self.category_links]

    @property
    def contact_slugs(self) -> list[str]:
        return [link.contact.slug for link in self.contact_links]
