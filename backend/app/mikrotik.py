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

    for method in (preferred, fallback):
        try:
            return connect(**base, login_method=method)
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
    When blocked=True, ensure MAC is in the address-list (firewall drops that list).
    When blocked=False, remove MAC from the list.
    """
    mac = normalize_mac(mac)
    with mikrotik_api() as api:
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


def ensure_firewall_drop_rule(list_name: str) -> str:
    """
    Ensure a forward drop rule exists for the address-list.
    Returns status message. Idempotent.
    """
    comment = f"internet-manager-drop:{list_name}"
    with mikrotik_api() as api:
        path = api.path("/ip/firewall/filter")
        for row in path:
            if row.get("comment") == comment:
                return "rule already exists"

        path.add(
            chain="forward",
            action="drop",
            **{"src-address-list": list_name},
            comment=comment,
        )
        return "rule created"
