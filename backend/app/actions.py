"""Shared device actions used by API and background scheduler."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app import adguard, mikrotik
from app.models import Device, SocialDomain


def apply_internet(db: Session, device: Device, blocked: bool) -> Device:
    if not mikrotik.is_configured():
        raise RuntimeError("MikroTik nie je nakonfigurovaný")
    rule_id = mikrotik.set_internet_blocked(
        device.address_list,
        device.mac,
        blocked,
        existing_rule_id=device.mikrotik_filter_id,
    )
    device.mikrotik_filter_id = rule_id
    device.internet_blocked = blocked
    device.internet_blocked_since = datetime.utcnow() if blocked else None
    db.commit()
    db.refresh(device)
    return device


def _social_domains(db: Session) -> list[str]:
    domains = [
        d.domain
        for d in db.query(SocialDomain).filter(SocialDomain.enabled.is_(True)).all()
    ]
    return domains or list(adguard.DEFAULT_SOCIAL_DOMAINS)


def apply_social_mode(db: Session, device: Device, mode: str) -> Device:
    """
    mode: on | slow | off
    - on: AdGuard unblock + no MikroTik throttle
    - slow: AdGuard unblock + MikroTik TLS throttle
    - off: AdGuard block + no MikroTik throttle
    """
    mode = mode.strip().lower()
    if mode not in {"on", "slow", "off"}:
        raise ValueError(f"Neznámy social mode: {mode}")

    want_block = mode == "off"
    want_slow = mode == "slow"

    ip = None
    if mikrotik.is_configured():
        try:
            ip = mikrotik.lookup_ip_for_mac(device.mac)
        except Exception:  # noqa: BLE001
            ip = None

    if want_block or (not want_block and device.social_blocked):
        if not adguard.is_configured() and want_block:
            raise RuntimeError("AdGuard nie je nakonfigurovaný")
        if adguard.is_configured():
            adguard.set_social_blocked(device.mac, want_block, _social_domains(db), ip=ip)

    if want_slow:
        if not mikrotik.is_configured():
            raise RuntimeError("MikroTik nie je nakonfigurovaný (potrebné pre SLOW)")
        mikrotik.set_social_slow(device.mac, True)
    elif device.social_slow or mikrotik.is_configured():
        if mikrotik.is_configured():
            try:
                mikrotik.set_social_slow(device.mac, False)
            except Exception:  # noqa: BLE001
                pass

    if want_block and ip and mikrotik.is_configured():
        try:
            mikrotik.kill_connections_for_ip(ip)
        except Exception:  # noqa: BLE001
            pass

    device.social_blocked = want_block
    device.social_slow = want_slow
    device.social_blocked_since = datetime.utcnow() if want_block else None
    db.commit()
    db.refresh(device)
    return device


def apply_schedule_action(db: Session, device: Device, action: str) -> Device:
    action = action.strip().lower()
    if action == "internet_on":
        return apply_internet(db, device, False)
    if action == "internet_off":
        return apply_internet(db, device, True)
    if action == "social_on":
        return apply_social_mode(db, device, "on")
    if action == "social_slow":
        return apply_social_mode(db, device, "slow")
    if action == "social_off":
        return apply_social_mode(db, device, "off")
    raise ValueError(f"Neznáma schedule akcia: {action}")
