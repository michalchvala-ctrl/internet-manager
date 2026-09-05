from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import actions, adguard, mikrotik
from app.auth import get_current_admin, get_current_user
from app.config import get_settings
from app.database import get_db
from app.models import Device, DeviceAccess, ScheduleRule, SocialDomain, TrafficDaily, User
from app.schemas import (
    DeviceCreate,
    DeviceOut,
    DeviceTrafficHistoryOut,
    DeviceUpdate,
    ScheduleRuleCreate,
    ScheduleRuleOut,
    ScheduleRuleUpdate,
    SocialDomainCreate,
    SocialDomainOut,
    StatusOut,
    ToggleRequest,
    TrafficDayOut,
)
from app import traffic as traffic_svc

router = APIRouter(prefix="/api", tags=["devices"])


def _normalize_mac_or_400(mac: str) -> str:
    try:
        return mikrotik.normalize_mac(mac)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _user_can_access_device(user: User, device: Device, db: Session) -> bool:
    if user.is_admin:
        return True
    return (
        db.query(DeviceAccess)
        .filter(DeviceAccess.user_id == user.id, DeviceAccess.device_id == device.id)
        .first()
        is not None
    )


def _devices_for_user(user: User, db: Session) -> list[Device]:
    q = db.query(Device).order_by(Device.sort_order, Device.name)
    if user.is_admin:
        return q.all()
    return (
        q.join(DeviceAccess, DeviceAccess.device_id == Device.id)
        .filter(DeviceAccess.user_id == user.id)
        .all()
    )


def _get_accessible_device(
    device_id: int,
    user: User,
    db: Session,
) -> Device:
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Zariadenie neexistuje")
    if not _user_can_access_device(user, device, db):
        raise HTTPException(status_code=403, detail="Nemáš prístup k tomuto zariadeniu")
    return device


@router.get("/status", response_model=StatusOut)
def status(_: Annotated[User, Depends(get_current_user)]) -> StatusOut:
    settings = get_settings()
    mt_cfg = mikrotik.is_configured()
    ag_cfg = adguard.is_configured()
    mt_ok, mt_err = (None, None)
    ag_ok, ag_err = (None, None)
    if mt_cfg:
        mt_ok, mt_err = mikrotik.ping()
    if ag_cfg:
        ag_ok, ag_err = adguard.ping()
    return StatusOut(
        mikrotik_configured=mt_cfg,
        mikrotik_ok=mt_ok,
        mikrotik_error=None if mt_ok else mt_err,
        adguard_configured=ag_cfg,
        adguard_ok=ag_ok,
        adguard_error=None if ag_ok else ag_err,
        mikrotik_webfig_url=settings.mikrotik_webfig_url or None,
        social_slow_limit_kbps=settings.social_slow_limit_kbps,
        timezone=settings.timezone,
    )


def _device_out(
    device: Device,
    traffic: dict[str, dict[str, int]] | None = None,
    today_row: TrafficDaily | None = None,
) -> DeviceOut:
    data = DeviceOut.model_validate(device)
    if traffic:
        stats = traffic.get(device.mac.upper()) or traffic.get(mikrotik.normalize_mac(device.mac))
        if stats:
            data.traffic_upload_bytes = stats.get("upload_bytes", 0)
            data.traffic_download_bytes = stats.get("download_bytes", 0)
    if today_row is not None:
        data.traffic_today_upload_bytes = int(today_row.upload_bytes or 0)
        data.traffic_today_download_bytes = int(today_row.download_bytes or 0)
    return data


@router.get("/devices", response_model=list[DeviceOut])
def list_devices(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[DeviceOut]:
    devices = _devices_for_user(user, db)
    traffic: dict[str, dict[str, int]] = {}
    today_map: dict[int, TrafficDaily] = {}
    if mikrotik.is_configured() and devices:
        try:
            for d in devices:
                if not d.mikrotik_queue_id:
                    try:
                        qid = mikrotik.ensure_traffic_accounting(d.mac, d.mikrotik_queue_id)
                        d.mikrotik_queue_id = qid
                    except Exception:  # noqa: BLE001
                        pass
            db.commit()
            traffic = mikrotik.get_traffic_by_mac([d.mac for d in devices])
            today_map = traffic_svc.sync_devices_traffic(db, devices, traffic)
        except Exception:  # noqa: BLE001
            traffic = {}
            today_map = {}
    return [_device_out(d, traffic, today_map.get(d.id)) for d in devices]


@router.post("/devices", response_model=DeviceOut, status_code=201)
def create_device(
    body: DeviceCreate,
    _: Annotated[User, Depends(get_current_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> DeviceOut:
    mac = _normalize_mac_or_400(body.mac)
    if db.query(Device).filter(Device.mac == mac).first():
        raise HTTPException(status_code=400, detail="Zariadenie s touto MAC už existuje")

    device = Device(
        name=body.name.strip(),
        mac=mac,
        address_list=body.address_list.strip(),
        category=body.category,
        sort_order=body.sort_order,
        notes=body.notes,
        owner_id=body.owner_id,
        created_at=datetime.utcnow(),
    )
    db.add(device)
    db.commit()
    db.refresh(device)

    if mikrotik.is_configured():
        try:
            rule_id = mikrotik.ensure_device_rule(
                device.address_list,
                device.mac,
                blocked=False,
            )
            device.mikrotik_filter_id = rule_id
            try:
                device.mikrotik_queue_id = mikrotik.ensure_traffic_accounting(device.mac)
            except Exception:  # noqa: BLE001
                pass
            db.commit()
            db.refresh(device)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=502,
                detail=f"Zariadenie uložené, ale firewall rule na MikroTiku zlyhalo: {exc}",
            ) from exc

    return _device_out(device)


@router.patch("/devices/{device_id}", response_model=DeviceOut)
def update_device(
    device_id: int,
    body: DeviceUpdate,
    _: Annotated[User, Depends(get_current_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> DeviceOut:
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Zariadenie neexistuje")

    data = body.model_dump(exclude_unset=True)
    if "mac" in data and data["mac"] is not None:
        data["mac"] = _normalize_mac_or_400(data["mac"])
        other = db.query(Device).filter(Device.mac == data["mac"], Device.id != device_id).first()
        if other:
            raise HTTPException(status_code=400, detail="MAC už používa iné zariadenie")

    for key, value in data.items():
        setattr(device, key, value)

    db.commit()
    db.refresh(device)
    return _device_out(device)


@router.delete("/devices/{device_id}", status_code=204)
def delete_device(
    device_id: int,
    _: Annotated[User, Depends(get_current_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Zariadenie neexistuje")
    if mikrotik.is_configured():
        try:
            mikrotik.remove_device_rule(device.mikrotik_filter_id, device.address_list, device.mac)
        except Exception:  # noqa: BLE001
            pass
        try:
            mikrotik.remove_traffic_accounting(device.mac)
        except Exception:  # noqa: BLE001
            pass
        try:
            mikrotik.set_social_slow(device.mac, False)
        except Exception:  # noqa: BLE001
            pass
    db.delete(device)
    db.commit()


@router.get("/devices/{device_id}/traffic", response_model=DeviceTrafficHistoryOut)
def device_traffic_history(
    device_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    days: int = 14,
) -> DeviceTrafficHistoryOut:
    device = _get_accessible_device(device_id, user, db)

    if mikrotik.is_configured():
        try:
            if not device.mikrotik_queue_id:
                device.mikrotik_queue_id = mikrotik.ensure_traffic_accounting(device.mac)
                db.commit()
            traffic = mikrotik.get_traffic_by_mac([device.mac])
            traffic_svc.sync_devices_traffic(db, [device], traffic)
        except Exception:  # noqa: BLE001
            pass

    rows = traffic_svc.history_for_device(db, device_id, days=min(max(days, 1), 90))
    return DeviceTrafficHistoryOut(
        device_id=device_id,
        days=[
            TrafficDayOut(
                day=r.day,
                upload_bytes=int(r.upload_bytes or 0),
                download_bytes=int(r.download_bytes or 0),
            )
            for r in rows
        ],
    )


@router.post("/devices/{device_id}/traffic/reset", response_model=DeviceOut)
def reset_device_traffic(
    device_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> DeviceOut:
    device = _get_accessible_device(device_id, user, db)
    if not mikrotik.is_configured():
        raise HTTPException(status_code=503, detail="MikroTik nie je nakonfigurovaný")
    try:
        if not device.mikrotik_queue_id:
            device.mikrotik_queue_id = mikrotik.ensure_traffic_accounting(device.mac)
            db.commit()
        traffic = mikrotik.get_traffic_by_mac([device.mac])
        today_map = traffic_svc.sync_devices_traffic(db, [device], traffic)
        mikrotik.reset_traffic_counters(device.mac)
        device.traffic_snap_upload = 0
        device.traffic_snap_download = 0
        device.traffic_snap_at = datetime.utcnow()
        db.commit()
        traffic = mikrotik.get_traffic_by_mac([device.mac])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"MikroTik chyba: {exc}") from exc
    return _device_out(device, traffic, today_map.get(device.id))


@router.post("/devices/{device_id}/internet", response_model=DeviceOut)
def toggle_internet(
    device_id: int,
    body: ToggleRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> DeviceOut:
    device = _get_accessible_device(device_id, user, db)
    try:
        device = actions.apply_internet(db, device, body.blocked)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"MikroTik chyba: {exc}") from exc
    return _device_out(device)


class SocialBody(BaseModel):
    """Accepts either {mode} or legacy {blocked}."""

    mode: str | None = None
    blocked: bool | None = None


@router.post("/devices/{device_id}/social", response_model=DeviceOut)
def toggle_social(
    device_id: int,
    body: SocialBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> DeviceOut:
    device = _get_accessible_device(device_id, user, db)

    if body.mode:
        mode = body.mode.strip().lower()
    elif body.blocked is not None:
        mode = "off" if body.blocked else "on"
    else:
        raise HTTPException(status_code=400, detail="Pošli mode (on|slow|off) alebo blocked")

    if mode not in {"on", "slow", "off"}:
        raise HTTPException(status_code=400, detail="mode musí byť on, slow alebo off")

    try:
        device = actions.apply_social_mode(db, device, mode)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Social chyba: {exc}") from exc
    return _device_out(device)


@router.get("/devices/{device_id}/schedules", response_model=list[ScheduleRuleOut])
def list_schedules(
    device_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[ScheduleRule]:
    _get_accessible_device(device_id, user, db)
    return (
        db.query(ScheduleRule)
        .filter(ScheduleRule.device_id == device_id)
        .order_by(ScheduleRule.time, ScheduleRule.id)
        .all()
    )


@router.post("/devices/{device_id}/schedules", response_model=ScheduleRuleOut, status_code=201)
def create_schedule(
    device_id: int,
    body: ScheduleRuleCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ScheduleRule:
    _get_accessible_device(device_id, user, db)
    rule = ScheduleRule(
        device_id=device_id,
        enabled=body.enabled,
        days=body.days.strip(),
        time=body.time.strip(),
        action=body.action,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@router.patch("/schedules/{rule_id}", response_model=ScheduleRuleOut)
def update_schedule(
    rule_id: int,
    body: ScheduleRuleUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ScheduleRule:
    rule = db.get(ScheduleRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Pravidlo neexistuje")
    _get_accessible_device(rule.device_id, user, db)
    data = body.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(rule, key, value)
    db.commit()
    db.refresh(rule)
    return rule


@router.delete("/schedules/{rule_id}", status_code=204)
def delete_schedule(
    rule_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    rule = db.get(ScheduleRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Pravidlo neexistuje")
    _get_accessible_device(rule.device_id, user, db)
    db.delete(rule)
    db.commit()


@router.get("/social-domains", response_model=list[SocialDomainOut])
def list_social_domains(
    _: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[SocialDomain]:
    return db.query(SocialDomain).order_by(SocialDomain.domain).all()


@router.post("/social-domains", response_model=SocialDomainOut, status_code=201)
def add_social_domain(
    body: SocialDomainCreate,
    _: Annotated[User, Depends(get_current_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> SocialDomain:
    domain = body.domain.strip().lower().lstrip(".")
    if db.query(SocialDomain).filter(SocialDomain.domain == domain).first():
        raise HTTPException(status_code=400, detail="Doména už existuje")
    row = SocialDomain(domain=domain, enabled=True)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/social-domains/{domain_id}", status_code=204)
def delete_social_domain(
    domain_id: int,
    _: Annotated[User, Depends(get_current_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    row = db.get(SocialDomain, domain_id)
    if not row:
        raise HTTPException(status_code=404, detail="Doména neexistuje")
    db.delete(row)
    db.commit()
