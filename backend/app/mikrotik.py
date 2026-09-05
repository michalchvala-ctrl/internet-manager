"""MikroTik RouterOS API – block internet per device MAC (not IP list)."""

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
    password = (s.mikrotik_password or "").strip()
    port = s.mikrotik_port

    base: dict[str, Any] = {
        "host": host,
        "username": username,
        "password": password,
        "port": port,
        "timeout": 10,
    }

    preferred = plain if s.mikrotik_plaintext_login else token
    fallback = token if s.mikrotik_plaintext_login else plain
    last_error: Exception | None = None

    try:
        return connect(**base, login_method=preferred)
    except TrapError:
        raise
    except Exception as exc:  # noqa: BLE001
        last_error = exc
        logger.warning("MikroTik login preferred failed: %s", exc)

    try:
        return connect(**base, login_method=fallback)
    except Exception as exc:  # noqa: BLE001
        last_error = exc
        logger.warning("MikroTik login fallback failed: %s", exc)

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
        pwd_len = len((s.mikrotik_password or "").strip())
        return False, f"{exc} | skúšam {user}@{host}:{s.mikrotik_port} (heslo dĺžka {pwd_len})"


def _rule_comment(list_name: str, mac: str) -> str:
    return f"internet-manager:{list_name}:{mac}"


def _has_interface_list(api: Any, name: str) -> bool:
    try:
        for row in api.path("/interface/list"):
            if row.get("name") == name:
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


def _forward_rules(api: Any) -> list[dict]:
    path = api.path("/ip/firewall/filter")
    rules: list[dict] = []
    for row in path:
        if row.get("chain") != "forward":
            continue
        # skip dynamic (fasttrack display etc.)
        if str(row.get("dynamic", "false")).lower() in {"true", "yes"}:
            continue
        rules.append(dict(row))
    return rules


def _find_place_before_id(api: Any) -> str | None:
    """
    Put our drop BEFORE the rule that accepts LAN→WAN / internet,
    otherwise accept wins and block never runs.
    """
    rules = _forward_rules(api)
    for row in rules:
        comment = str(row.get("comment") or "").lower()
        action = str(row.get("action") or "")
        out_list = str(row.get("out-interface-list") or "")
        if action != "accept":
            continue
        if out_list.upper() == "WAN":
            return row[".id"]
        if "defconf: accept" in comment and "wan" in comment:
            return row[".id"]
        if "accept" in comment and "wan" in comment:
            return row[".id"]

    # Fallback: before first catch-all drop at end
    for row in rules:
        if row.get("action") == "drop" and not row.get("src-mac-address") and not row.get(
            "src-address-list"
        ):
            comment = str(row.get("comment") or "").lower()
            if "internet-manager" in comment:
                continue
            return row[".id"]

    # Last resort: before first forward rule
    if rules:
        return rules[0][".id"]
    return None


def _find_mac_rules(api: Any, list_name: str, mac: str) -> list[dict]:
    comment = _rule_comment(list_name, mac)
    found = []
    for row in _forward_rules(api):
        if row.get("comment") == comment:
            found.append(row)
            continue
        # also match by MAC alone (legacy / renamed list)
        if str(row.get("src-mac-address", "")).upper() == mac.upper() and str(
            row.get("comment") or ""
        ).startswith("internet-manager:"):
            found.append(row)
    return found


def _lookup_ip_for_mac(api: Any, mac: str) -> str | None:
    mac_n = normalize_mac(mac)
    # DHCP leases
    try:
        for row in api.path("/ip/dhcp-server/lease"):
            active = str(row.get("active-mac-address") or row.get("mac-address") or "").upper()
            if active.replace("-", ":") == mac_n:
                ip = row.get("active-address") or row.get("address")
                if ip:
                    return str(ip)
    except Exception:  # noqa: BLE001
        logger.exception("DHCP lease lookup failed")

    # ARP fallback
    try:
        for row in api.path("/ip/arp"):
            if str(row.get("mac-address", "")).upper().replace("-", ":") == mac_n:
                ip = row.get("address")
                if ip:
                    return str(ip)
    except Exception:  # noqa: BLE001
        logger.exception("ARP lookup failed")
    return None


def set_internet_blocked(list_name: str, mac: str, blocked: bool) -> None:
    """
    Block internet for a device by MAC using IP firewall forward + src-mac-address.
    Also mirrors current IP into address-list (helps some setups).
    """
    mac = normalize_mac(mac)
    list_name = list_name.strip()
    comment = _rule_comment(list_name, mac)

    with mikrotik_api() as api:
        path = api.path("/ip/firewall/filter")
        existing_rules = _find_mac_rules(api, list_name, mac)

        if blocked:
            if not existing_rules:
                params: dict[str, Any] = {
                    "chain": "forward",
                    "action": "drop",
                    "src-mac-address": mac,
                    "comment": comment,
                }
                if _has_interface_list(api, "WAN"):
                    params["out-interface-list"] = "WAN"

                place_before = _find_place_before_id(api)
                if place_before:
                    params["place-before"] = place_before

                try:
                    path.add(**params)
                    logger.info("Added MAC drop rule %s before=%s", params, place_before)
                except TrapError as exc:
                    # retry without place-before
                    params.pop("place-before", None)
                    logger.warning("place-before failed (%s), retry plain add", exc)
                    path.add(**params)

            # Mirror IP into address-list (optional helper)
            ip = _lookup_ip_for_mac(api, mac)
            if ip:
                alist = api.path("/ip/firewall/address-list")
                already = False
                for row in alist:
                    if row.get("list") == list_name and str(row.get("address")) == ip:
                        already = True
                        break
                if not already:
                    try:
                        alist.add(
                            list=list_name,
                            address=ip,
                            comment=f"internet-manager:{mac}",
                        )
                    except TrapError:
                        pass
        else:
            for row in existing_rules:
                path.remove(row[".id"])

            # Remove mirrored IPs for this MAC comment
            alist = api.path("/ip/firewall/address-list")
            for row in list(alist):
                if row.get("list") == list_name and str(row.get("comment") or "") == f"internet-manager:{mac}":
                    try:
                        alist.remove(row[".id"])
                    except TrapError:
                        pass


def ensure_firewall_drop_rule(list_name: str) -> str:
    """
    Kept for device-create compatibility. Real block rules are per-MAC on toggle.
    Ensures a shared list-based WAN drop exists as extra safety when IPs are mirrored.
    """
    comment = f"internet-manager-drop:{list_name}"
    with mikrotik_api() as api:
        path = api.path("/ip/firewall/filter")
        for row in _forward_rules(api):
            if row.get("comment") == comment:
                return "rule already exists"

        params: dict[str, Any] = {
            "chain": "forward",
            "action": "drop",
            "src-address-list": list_name,
            "comment": comment,
        }
        if _has_interface_list(api, "WAN"):
            params["out-interface-list"] = "WAN"

        place_before = _find_place_before_id(api)
        if place_before:
            params["place-before"] = place_before

        try:
            path.add(**params)
        except TrapError:
            params.pop("place-before", None)
            path.add(**params)
        return "rule created"
