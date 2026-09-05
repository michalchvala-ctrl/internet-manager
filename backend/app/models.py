from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    devices: Mapped[list["Device"]] = relationship(back_populates="owner")


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("mac", name="uq_device_mac"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    mac: Mapped[str] = mapped_column(String(17), index=True)
    address_list: Mapped[str] = mapped_column(String(128))  # MikroTik address-list name
    category: Mapped[str] = mapped_column(String(32), default="other")  # child|pc|tv|other
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Desired / last-known state (synced from MikroTik + AdGuard on toggle)
    internet_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    social_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    internet_blocked_since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    social_blocked_since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    owner: Mapped[User | None] = relationship(back_populates="devices")


class SocialDomain(Base):
    """Domains blocked when social mode is ON for a device (via AdGuard or MikroTik)."""

    __tablename__ = "social_domains"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
