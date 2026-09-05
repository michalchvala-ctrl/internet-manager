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
    device_access: Mapped[list["DeviceAccess"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("mac", name="uq_device_mac"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    mac: Mapped[str] = mapped_column(String(17), index=True)
    address_list: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(32), default="other")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    internet_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    social_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    internet_blocked_since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    social_blocked_since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    mikrotik_filter_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    mikrotik_queue_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    social_slow: Mapped[bool] = mapped_column(Boolean, default=False)
    # Last seen cumulative MikroTik queue counters (for daily delta)
    traffic_snap_upload: Mapped[int] = mapped_column(Integer, default=0)
    traffic_snap_download: Mapped[int] = mapped_column(Integer, default=0)
    traffic_snap_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    owner: Mapped[User | None] = relationship(back_populates="devices")
    viewers: Mapped[list["DeviceAccess"]] = relationship(
        back_populates="device",
        cascade="all, delete-orphan",
    )
    traffic_days: Mapped[list["TrafficDaily"]] = relationship(
        back_populates="device",
        cascade="all, delete-orphan",
    )
    traffic_hours: Mapped[list["TrafficHourly"]] = relationship(
        back_populates="device",
        cascade="all, delete-orphan",
    )
    schedule_rules: Mapped[list["ScheduleRule"]] = relationship(
        back_populates="device",
        cascade="all, delete-orphan",
    )


class DeviceAccess(Base):
    """Which non-admin users can see/control a device."""

    __tablename__ = "device_access"
    __table_args__ = (UniqueConstraint("user_id", "device_id", name="uq_user_device"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)

    user: Mapped[User] = relationship(back_populates="device_access")
    device: Mapped[Device] = relationship(back_populates="viewers")


class SocialDomain(Base):
    __tablename__ = "social_domains"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class TrafficDaily(Base):
    """Bytes transferred per device per calendar day (Europe/Bratislava)."""

    __tablename__ = "traffic_daily"
    __table_args__ = (UniqueConstraint("device_id", "day", name="uq_device_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    day: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD
    upload_bytes: Mapped[int] = mapped_column(Integer, default=0)
    download_bytes: Mapped[int] = mapped_column(Integer, default=0)

    device: Mapped[Device] = relationship(back_populates="traffic_days")


class TrafficHourly(Base):
    """Bytes per device per hour – for in-app day graph (Europe/Bratislava)."""

    __tablename__ = "traffic_hourly"
    __table_args__ = (UniqueConstraint("device_id", "day", "hour", name="uq_device_day_hour"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    day: Mapped[str] = mapped_column(String(10), index=True)
    hour: Mapped[int] = mapped_column(Integer)  # 0–23
    upload_bytes: Mapped[int] = mapped_column(Integer, default=0)
    download_bytes: Mapped[int] = mapped_column(Integer, default=0)

    device: Mapped[Device] = relationship(back_populates="traffic_hours")


class ScheduleRule(Base):
    """Weekly timed action for a device – executed by background scheduler in Docker."""

    __tablename__ = "schedule_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Python weekday: 0=Mon .. 6=Sun, comma-separated e.g. "0,1,2,3,4"
    days: Mapped[str] = mapped_column(String(32), default="0,1,2,3,4,5,6")
    time: Mapped[str] = mapped_column(String(5))  # HH:MM
    # internet_on|internet_off|social_on|social_slow|social_off
    action: Mapped[str] = mapped_column(String(32))
    last_fired: Mapped[str | None] = mapped_column(String(32), nullable=True)

    device: Mapped[Device] = relationship(back_populates="schedule_rules")
