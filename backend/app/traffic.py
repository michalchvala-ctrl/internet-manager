"""Daily + hourly traffic aggregation from MikroTik queue counters."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models import Device, TrafficDaily, TrafficHourly

TZ = ZoneInfo("Europe/Bratislava")


def local_today() -> date:
    return datetime.now(TZ).date()


def local_now() -> datetime:
    return datetime.now(TZ)


def _get_or_create_day(db: Session, device_id: int, day: date) -> TrafficDaily:
    row = (
        db.query(TrafficDaily)
        .filter(TrafficDaily.device_id == device_id, TrafficDaily.day == day.isoformat())
        .first()
    )
    if row:
        return row
    row = TrafficDaily(
        device_id=device_id,
        day=day.isoformat(),
        upload_bytes=0,
        download_bytes=0,
    )
    db.add(row)
    db.flush()
    return row


def _get_or_create_hour(
    db: Session,
    device_id: int,
    day: date,
    hour: int,
) -> TrafficHourly:
    day_s = day.isoformat()
    row = (
        db.query(TrafficHourly)
        .filter(
            TrafficHourly.device_id == device_id,
            TrafficHourly.day == day_s,
            TrafficHourly.hour == hour,
        )
        .first()
    )
    if row:
        return row
    row = TrafficHourly(
        device_id=device_id,
        day=day_s,
        hour=hour,
        upload_bytes=0,
        download_bytes=0,
    )
    db.add(row)
    db.flush()
    return row


def apply_counter_delta(
    db: Session,
    device: Device,
    upload_total: int,
    download_total: int,
) -> TrafficDaily:
    """
    Compare MikroTik cumulative counters to last snapshot, add delta to today's
    day + current hour buckets (Europe/Bratislava).
    """
    now = local_now()
    today = now.date()
    hour = now.hour
    prev_up = int(device.traffic_snap_upload or 0)
    prev_down = int(device.traffic_snap_download or 0)

    if upload_total < prev_up or download_total < prev_down:
        delta_up = max(0, upload_total)
        delta_down = max(0, download_total)
    else:
        delta_up = max(0, upload_total - prev_up)
        delta_down = max(0, download_total - prev_down)

    day_row = _get_or_create_day(db, device.id, today)
    hour_row = _get_or_create_hour(db, device.id, today, hour)
    if delta_up or delta_down:
        day_row.upload_bytes = int(day_row.upload_bytes) + delta_up
        day_row.download_bytes = int(day_row.download_bytes) + delta_down
        hour_row.upload_bytes = int(hour_row.upload_bytes) + delta_up
        hour_row.download_bytes = int(hour_row.download_bytes) + delta_down

    device.traffic_snap_upload = upload_total
    device.traffic_snap_download = download_total
    device.traffic_snap_at = datetime.utcnow()
    return day_row


def sync_devices_traffic(
    db: Session,
    devices: list[Device],
    traffic_by_mac: dict[str, dict[str, int]],
) -> dict[int, TrafficDaily]:
    """Update daily/hourly totals for all devices; return today's row per device_id."""
    today_rows: dict[int, TrafficDaily] = {}
    for device in devices:
        mac = device.mac.upper()
        stats = traffic_by_mac.get(mac) or traffic_by_mac.get(device.mac) or {}
        up = int(stats.get("upload_bytes") or 0)
        down = int(stats.get("download_bytes") or 0)
        today_rows[device.id] = apply_counter_delta(db, device, up, down)
    db.commit()
    return today_rows


def history_for_device(db: Session, device_id: int, days: int = 14) -> list[TrafficDaily]:
    start = (local_today() - timedelta(days=max(0, days - 1))).isoformat()
    return (
        db.query(TrafficDaily)
        .filter(TrafficDaily.device_id == device_id, TrafficDaily.day >= start)
        .order_by(TrafficDaily.day.asc())
        .all()
    )


def hours_for_device_day(db: Session, device_id: int, day: date | None = None) -> list[TrafficHourly]:
    day_s = (day or local_today()).isoformat()
    return (
        db.query(TrafficHourly)
        .filter(TrafficHourly.device_id == device_id, TrafficHourly.day == day_s)
        .order_by(TrafficHourly.hour.asc())
        .all()
    )
