"""Background weekly schedules – runs inside Docker even when the PWA is closed."""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from app.actions import apply_schedule_action
from app.config import get_settings
from app.database import SessionLocal
from app.models import ScheduleRule

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _tick() -> None:
    settings = get_settings()
    try:
        tz = ZoneInfo(settings.timezone)
    except Exception:  # noqa: BLE001
        tz = ZoneInfo("Europe/Bratislava")

    now = datetime.now(tz)
    hhmm = now.strftime("%H:%M")
    weekday = str(now.weekday())  # 0=Mon
    fire_day = now.strftime("%Y-%m-%d")

    db = SessionLocal()
    try:
        rules = (
            db.query(ScheduleRule)
            .filter(ScheduleRule.enabled.is_(True))
            .all()
        )
        for rule in rules:
            days = {d.strip() for d in (rule.days or "").split(",") if d.strip()}
            if weekday not in days:
                continue
            if (rule.time or "").strip() != hhmm:
                continue
            fire_key = f"{fire_day}T{hhmm}"
            if rule.last_fired == fire_key:
                continue
            device = rule.device
            if not device:
                continue
            try:
                logger.info(
                    "Schedule fire rule=%s device=%s action=%s",
                    rule.id,
                    device.name,
                    rule.action,
                )
                rule_id = rule.id
                apply_schedule_action(db, device, rule.action)
                row = db.get(ScheduleRule, rule_id)
                if row:
                    row.last_fired = fire_key
                    db.commit()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Schedule rule %s failed for device %s",
                    rule.id,
                    getattr(device, "id", "?"),
                )
                db.rollback()
    finally:
        db.close()


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(timezone=get_settings().timezone)
    _scheduler.add_job(_tick, "cron", second=5, id="schedule-tick", replace_existing=True)
    _scheduler.start()
    logger.info("APScheduler started (timezone=%s)", get_settings().timezone)


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    _scheduler.shutdown(wait=False)
    _scheduler = None
