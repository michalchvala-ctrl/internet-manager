"""MikroTik RouterOS API – block internet per MAC via address-list."""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from typing import Any, Iterator

from librouteros import connect
from librouteros.exceptions import TrapError
from librouteros.login import plain, token

from app.config import get_settings

logger = logging.getLogger(__name__)

MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$")


def normalize_mac(mac: str) -> str:
    mac = mac.strip().upper().replace("-", ":")
    if not MAC_RE.match(mac):
        raise ValueError(f"Neplatná MAC adresa: {mac}")
    return mac


def is_configured() -> bool:
    s = get_settings()
    return bool(s.mikrotik_host.strip() and s.mikrotik_user.strip())


def _connect() -> Any:
    s = get_settings()
    host = s.mikrotik_host.strip()
    username = s.mikrotik_user.strip()
    password = s.mikrotik_password  # don't strip middle; only edges
    password = password.strip() if password else ""
    port = s.mikrotik_port

    base: dict[str, Any] = {
        "host": host,
        "username": username,
        "password": password,
        "port": port,
        "timeout": 10,
    }

    # RouterOS 6.43+ = plain; staršie = token. Skús podľa nastavenia, potom fallback.
    preferred = plain if s.mikrotik_plaintext_login else token
    fallback = token if s.mikrotik_plaintext_login else plain
    last_error: Exception | None = None

    for method in (preferred,):
        try:
            return connect(**base, login_method=method)
        except TrapError as exc:
            # Auth error – second method rarely helps; fail fast
            if "password" in str(exc).lower() or "user" in str(exc).lower():
                raise
            last_error = exc
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning(
                "MikroTik login failed host=%s port=%s user=%s method=%s err=%s",
                host,
                port,
                username,
                getattr(method, "__name__", method),
                exc,
            )

    # One fallback login method if preferred failed non-auth
    try:
        return connect(**base, login_method=fallback)
    except Exception as exc:  # noqa: BLE001
        last_error = exc
        logger.warning(
            "MikroTik login failed host=%s port=%s user=%s method=%s err=%s",
            host,
            port,
            username,
            getattr(fallback, "__name__", fallback),
            exc,
        )

    assert last_error is not None
    raise last_error


@contextmanager
def mikrotik_api() -> Iterator[Any]:
    if not is_configured():
        raise RuntimeError("MikroTik nie je nakonfigurovaný (MIKROTIK_HOST / USER / PASSWORD)")

    api = _connect()
    try:
        yield api
    finally:
        try:
            api.close()
        except Exception:  # noqa: BLE001
            pass


def ping() -> tuple[bool, str | None]:
    if not is_configured():
        return False, "Nie je nakonfigurovaný"
    s = get_settings()
    user = s.mikrotik_user.strip()
    host = s.mikrotik_host.strip()
    try:
        with mikrotik_api() as api:
            identity = list(api.path("/system/identity").select("name"))
            name = identity[0].get("name") if identity else "?"
            return True, f"OK ({name})"
    except Exception as exc:  # noqa: BLE001
        logger.exception("MikroTik ping failed")
        pwd_len = len(s.mikrotik_password.strip()) if s.mikrotik_password else 0
        return (
            False,
            f"{exc} | skúšam {user}@{host}:{s.mikrotik_port} (heslo dĺžka {pwd_len})",
        )


def _find_list_entries(api: Any, list_name: str, address: str | None = None) -> list[dict]:
    path = api.path("/ip/firewall/address-list")
    try:
        query = path.select(".id", "list", "address").where(list=list_name)
        if address is not None:
            query = query.where(address=address)
        return [dict(row) for row in query]
    except Exception:  # noqa: BLE001
        # Fallback for older RouterOS / librouteros without where()
        entries = []
        for row in path:
            if row.get("list") != list_name:
                continue
            if address is not None and str(row.get("address", "")).upper() != address.upper():
                continue
            entries.append(dict(row))
        return entries


def is_mac_in_list(list_name: str, mac: str) -> bool:
    mac = normalize_mac(mac)
    with mikrotik_api() as api:
        return len(_find_list_entries(api, list_name, mac)) > 0


def set_internet_blocked(list_name: str, mac: str, blocked: bool) -> None:
    """
    When blocked=True, ensure firewall drop rule exists and MAC is in address-list.
    When blocked=False, remove MAC from the list (Wi‑Fi/LAN stay up).
    """
    mac = normalize_mac(mac)
    with mikrotik_api() as api:
        if blocked:
            _ensure_firewall_drop_rule_on_api(api, list_name)

        path = api.path("/ip/firewall/address-list")
        existing = _find_list_entries(api, list_name, mac)

        if blocked:
            if existing:
                return
            try:
                path.add(list=list_name, address=mac, comment=f"internet-manager:{list_name}")
            except TrapError as exc:
                if "already have" not in str(exc).lower():
                    raise
        else:
            for entry in existing:
                path.remove(entry[".id"])


def _has_interface_list(api: Any, name: str) -> bool:
    try:
        for row in api.path("/interface/list"):
            if row.get("name") == name:
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


def _ensure_firewall_drop_rule_on_api(api: Any, list_name: str) -> str:
    """
    Idempotent: forward drop for src-address-list, ideally only toward WAN
    so LAN/HA keeps working.
    """
    comment = f"internet-manager-drop:{list_name}"
    path = api.path("/ip/firewall/filter")

    try:
        existing = list(path.select(".id", "comment").where(comment=comment))
        if existing:
            return "rule already exists"
    except Exception:  # noqa: BLE001
        for row in path:
            if row.get("comment") == comment:
                return "rule already exists"

    params: dict[str, Any] = {
        "chain": "forward",
        "action": "drop",
        "src-address-list": list_name,
        "comment": comment,
    }
    # Prefer WAN-only drop so LAN stays up
    if _has_interface_list(api, "WAN"):
        params["out-interface-list"] = "WAN"

    path.add(**params)
    logger.info("Created MikroTik firewall rule for list=%s params=%s", list_name, params)
    return "rule created"


def ensure_firewall_drop_rule(list_name: str) -> str:
    """Ensure a forward drop rule exists for the address-list. Idempotent."""
    with mikrotik_api() as api:
        return _ensure_firewall_drop_rule_on_api(api, list_name)
