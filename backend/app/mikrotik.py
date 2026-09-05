"""MikroTik RouterOS API – one filter rule per device, enable/disable on toggle."""

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

_HAS_WAN_LIST: bool | None = None
_PLACE_BEFORE_ID: str | None = None
_PLACE_BEFORE_RESOLVED = False


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
    base: dict[str, Any] = {
        "host": s.mikrotik_host.strip(),
        "username": s.mikrotik_user.strip(),
        "password": (s.mikrotik_password or "").strip(),
        "port": s.mikrotik_port,
        "timeout": 8,
        "login_method": plain if s.mikrotik_plaintext_login else token,
    }
    try:
        return connect(**base)
    except TrapError:
        raise
    except Exception:
        base["login_method"] = token if s.mikrotik_plaintext_login else plain
        return connect(**base)


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
    try:
        with mikrotik_api() as api:
            identity = list(api.path("/system/identity").select("name"))
            name = identity[0].get("name") if identity else "?"
            return True, f"OK ({name})"
    except Exception as exc:  # noqa: BLE001
        logger.exception("MikroTik ping failed")
        return (
            False,
            f"{exc} | {s.mikrotik_user.strip()}@{s.mikrotik_host.strip()}:{s.mikrotik_port}",
        )


def rule_comment(list_name: str, mac: str) -> str:
    return f"internet-manager:{list_name.strip()}:{normalize_mac(mac)}"


def _has_wan_list(api: Any) -> bool:
    global _HAS_WAN_LIST
    if _HAS_WAN_LIST is not None:
        return _HAS_WAN_LIST
    try:
        _HAS_WAN_LIST = any(row.get("name") == "WAN" for row in api.path("/interface/list"))
    except Exception:  # noqa: BLE001
        _HAS_WAN_LIST = False
    return _HAS_WAN_LIST


def _resolve_place_before(api: Any) -> str | None:
    global _PLACE_BEFORE_ID, _PLACE_BEFORE_RESOLVED
    if _PLACE_BEFORE_RESOLVED:
        return _PLACE_BEFORE_ID

    place: str | None = None
    drop_fallback: str | None = None
    first_id: str | None = None

    for row in api.path("/ip/firewall/filter"):
        if row.get("chain") != "forward":
            continue
        if str(row.get("dynamic", "false")).lower() in {"true", "yes"}:
            continue

        rid = row.get(".id")
        if first_id is None:
            first_id = rid

        comment = str(row.get("comment") or "").lower()
        action = str(row.get("action") or "")
        out_list = str(row.get("out-interface-list") or "")

        if action == "accept" and (
            out_list.upper() == "WAN" or ("wan" in comment and "accept" in comment)
        ):
            place = rid
            break

        if (
            drop_fallback is None
            and action == "drop"
            and "internet-manager" not in comment
            and not row.get("src-mac-address")
        ):
            drop_fallback = rid

    _PLACE_BEFORE_ID = place or drop_fallback or first_id
    _PLACE_BEFORE_RESOLVED = True
    return _PLACE_BEFORE_ID


def _set_disabled(api: Any, rule_id: str, disabled: bool) -> None:
    """disabled=True means internet ALLOWED (rule off)."""
    path = api.path("/ip/firewall/filter")
    path.update(**{".id": rule_id, "disabled": "yes" if disabled else "no"})


def _rule_exists(api: Any, rule_id: str) -> bool:
    for row in api.path("/ip/firewall/filter"):
        if row.get(".id") == rule_id:
            return True
    return False


def _find_id_by_comment(api: Any, comment: str) -> str | None:
    for row in api.path("/ip/firewall/filter"):
        if row.get("comment") == comment:
            return row.get(".id")
    return None


def ensure_device_rule(
    list_name: str,
    mac: str,
    *,
    blocked: bool = False,
    existing_rule_id: str | None = None,
) -> str:
    """
    Ensure a permanent forward drop rule for this MAC exists.
    Returns MikroTik .id. Rule is enabled when blocked=True, disabled when False.
    Fast path: if we already know .id, only enable/disable (no firewall scan).
    """
    mac = normalize_mac(mac)
    comment = rule_comment(list_name, mac)

    with mikrotik_api() as api:
        path = api.path("/ip/firewall/filter")

        # Fast path – no scanning
        if existing_rule_id:
            try:
                _set_disabled(api, existing_rule_id, disabled=not blocked)
                return existing_rule_id
            except TrapError:
                logger.warning("Stored rule id %s missing, recreating", existing_rule_id)

        rule_id = _find_id_by_comment(api, comment)

        if not rule_id:
            params: dict[str, Any] = {
                "chain": "forward",
                "action": "drop",
                "src-mac-address": mac,
                "comment": comment,
                "disabled": "no" if blocked else "yes",
            }
            if _has_wan_list(api):
                params["out-interface-list"] = "WAN"
            place_before = _resolve_place_before(api)
            if place_before:
                params["place-before"] = place_before
            try:
                ret = path.add(**params)
            except TrapError:
                params.pop("place-before", None)
                ret = path.add(**params)

            if isinstance(ret, str):
                rule_id = ret
            elif isinstance(ret, dict) and ret.get("ret"):
                rule_id = str(ret["ret"])
            else:
                rule_id = _find_id_by_comment(api, comment)
            if not rule_id:
                raise RuntimeError("MikroTik rule vytvorené, ale neviem získať .id")
            logger.info("Created permanent rule %s for %s (blocked=%s)", rule_id, mac, blocked)
        else:
            _set_disabled(api, rule_id, disabled=not blocked)

        return rule_id


def set_internet_blocked(
    list_name: str,
    mac: str,
    blocked: bool,
    *,
    existing_rule_id: str | None = None,
) -> str:
    """Fast path: enable/disable existing rule. Creates it once if missing. Returns rule id."""
    return ensure_device_rule(
        list_name,
        mac,
        blocked=blocked,
        existing_rule_id=existing_rule_id,
    )


def remove_device_rule(rule_id: str | None, list_name: str, mac: str) -> None:
    if not is_configured():
        return
    comment = rule_comment(list_name, mac)
    with mikrotik_api() as api:
        path = api.path("/ip/firewall/filter")
        target = rule_id
        if not target or not _rule_exists(api, target):
            target = _find_id_by_comment(api, comment)
        if target:
            path.remove(target)


def ensure_firewall_drop_rule(list_name: str) -> str:
    """Legacy no-op helper kept for imports; per-device MAC rules are preferred."""
    return f"ok:{list_name}"
