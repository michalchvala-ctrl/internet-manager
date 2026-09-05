from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import adguard, mikrotik
from app.auth import get_current_admin, get_current_user
from app.database import get_db
from app.models import Device, DeviceAccess, SocialDomain, User
from app.schemas import (
    DeviceCreate,
    DeviceOut,
    DeviceUpdate,
    SocialDomainCreate,
    SocialDomainOut,
    StatusOut,
    ToggleRequest,
)

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


@router.get("/status", response_model=StatusOut)
def status(_: Annotated[User, Depends(get_current_user)]) -> StatusOut:
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
    )


def _device_out(device: Device, traffic: dict[str, dict[str, int]] | None = None) -> DeviceOut:
    data = DeviceOut.model_validate(device)
    if traffic:
        stats = traffic.get(device.mac.upper()) or traffic.get(mikrotik.normalize_mac(device.mac))
        if stats:
            data.traffic_upload_bytes = stats.get("upload_bytes", 0)
            data.traffic_download_bytes = stats.get("download_bytes", 0)
    return data


@router.get("/devices", response_model=list[DeviceOut])
def list_devices(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[DeviceOut]:
    devices = _devices_for_user(user, db)
    traffic: dict[str, dict[str, int]] = {}
    if mikrotik.is_configured() and devices:
        try:
            # Ensure accounting exists for older devices (best-effort)
            for d in devices:
                if not d.mikrotik_queue_id:
                    try:
                        qid = mikrotik.ensure_traffic_accounting(d.mac, d.mikrotik_queue_id)
                        d.mikrotik_queue_id = qid
                    except Exception:  # noqa: BLE001
                        pass
            db.commit()
            traffic = mikrotik.get_traffic_by_mac([d.mac for d in devices])
        except Exception:  # noqa: BLE001
            traffic = {}
    return [_device_out(d, traffic) for d in devices]


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
) -> Device:
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
    return device


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
    db.delete(device)
    db.commit()


@router.post("/devices/{device_id}/traffic/reset", response_model=DeviceOut)
def reset_device_traffic(
    device_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> DeviceOut:
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Zariadenie neexistuje")
    if not _user_can_access_device(user, device, db):
        raise HTTPException(status_code=403, detail="Nemáš prístup k tomuto zariadeniu")
    if not mikrotik.is_configured():
        raise HTTPException(status_code=503, detail="MikroTik nie je nakonfigurovaný")
    try:
        if not device.mikrotik_queue_id:
            device.mikrotik_queue_id = mikrotik.ensure_traffic_accounting(device.mac)
            db.commit()
        mikrotik.reset_traffic_counters(device.mac)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"MikroTik chyba: {exc}") from exc
    traffic = mikrotik.get_traffic_by_mac([device.mac])
    return _device_out(device, traffic)


@router.post("/devices/{device_id}/internet", response_model=DeviceOut)
def toggle_internet(
    device_id: int,
    body: ToggleRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Device:
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Zariadenie neexistuje")
    if not _user_can_access_device(user, device, db):
        raise HTTPException(status_code=403, detail="Nemáš prístup k tomuto zariadeniu")

    if not mikrotik.is_configured():
        raise HTTPException(
            status_code=503,
            detail="MikroTik nie je nakonfigurovaný – nastav MIKROTIK_* v .env",
        )

    try:
        rule_id = mikrotik.set_internet_blocked(
            device.address_list,
            device.mac,
            body.blocked,
            existing_rule_id=device.mikrotik_filter_id,
        )
        device.mikrotik_filter_id = rule_id
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"MikroTik chyba: {exc}") from exc

    device.internet_blocked = body.blocked
    device.internet_blocked_since = datetime.utcnow() if body.blocked else None
    db.commit()
    db.refresh(device)
    return device


@router.post("/devices/{device_id}/social", response_model=DeviceOut)
def toggle_social(
    device_id: int,
    body: ToggleRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Device:
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Zariadenie neexistuje")
    if not _user_can_access_device(user, device, db):
        raise HTTPException(status_code=403, detail="Nemáš prístup k tomuto zariadeniu")

    if not adguard.is_configured():
        raise HTTPException(
            status_code=503,
            detail="AdGuard nie je nakonfigurovaný – nastav ADGUARD_* v .env (alebo použij len Internet OFF)",
        )

    domains = [
        d.domain
        for d in db.query(SocialDomain).filter(SocialDomain.enabled.is_(True)).all()
    ]
    if not domains:
        domains = adguard.DEFAULT_SOCIAL_DOMAINS

    try:
        ip = None
        if mikrotik.is_configured():
            try:
                ip = mikrotik.lookup_ip_for_mac(device.mac)
            except Exception:  # noqa: BLE001
                ip = None
        adguard.set_social_blocked(device.mac, body.blocked, domains, ip=ip)
        if body.blocked and ip and mikrotik.is_configured():
            try:
                mikrotik.kill_connections_for_ip(ip)
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"AdGuard chyba: {exc}") from exc

    device.social_blocked = body.blocked
    device.social_blocked_since = datetime.utcnow() if body.blocked else None
    db.commit()
    db.refresh(device)
    return device


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
