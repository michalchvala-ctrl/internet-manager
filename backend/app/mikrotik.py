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
    """
    Must sit BEFORE accept established/related and BEFORE LAN→WAN accept,
    otherwise YouTube/keep-alive streams keep flowing.
    Prefer placing before fasttrack / established.
    """
    global _PLACE_BEFORE_ID, _PLACE_BEFORE_RESOLVED
    if _PLACE_BEFORE_RESOLVED:
        return _PLACE_BEFORE_ID

    established_id: str | None = None
    wan_accept_id: str | None = None
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
        conn = str(row.get("connection-state") or "").lower()
        out_list = str(row.get("out-interface-list") or "")

        if established_id is None and action in {"accept", "fasttrack-connection"}:
            if (
                "established" in conn
                or "related" in conn
                or "fasttrack" in comment
                or "established" in comment
                or action == "fasttrack-connection"
            ):
                established_id = rid

        if wan_accept_id is None and action == "accept" and (
            out_list.upper() == "WAN" or ("wan" in comment and "accept" in comment)
        ):
            wan_accept_id = rid

    # earliest critical rule wins
    _PLACE_BEFORE_ID = established_id or wan_accept_id or first_id
    _PLACE_BEFORE_RESOLVED = True
    return _PLACE_BEFORE_ID


def _set_disabled(api: Any, rule_id: str, disabled: bool) -> None:
    """disabled=True means internet ALLOWED (rule off)."""
    api.path("/ip/firewall/filter").update(
        **{".id": rule_id, "disabled": "yes" if disabled else "no"}
    )


def _move_before(api: Any, rule_id: str, place_before: str | None) -> None:
    if not place_before or place_before == rule_id:
        return
    try:
        # RouterOS move: numbers=<id> destination=<id>
        api.path("/ip/firewall/filter").move(**{"numbers": rule_id, "destination": place_before})
    except Exception:  # noqa: BLE001
        try:
            api(
                "/ip/firewall/filter/move",
                **{"numbers": rule_id, "destination": place_before},
            )
        except Exception:  # noqa: BLE001
            logger.warning("Could not move rule %s before %s", rule_id, place_before, exc_info=True)


def _lookup_ip_for_mac(api: Any, mac: str) -> str | None:
    mac_n = normalize_mac(mac)
    try:
        for row in api.path("/ip/dhcp-server/lease"):
            active = str(row.get("active-mac-address") or row.get("mac-address") or "")
            if active.upper().replace("-", ":") == mac_n:
                ip = row.get("active-address") or row.get("address")
                if ip:
                    return str(ip).split("/")[0]
    except Exception:  # noqa: BLE001
        logger.warning("DHCP lookup failed", exc_info=True)
    try:
        for row in api.path("/ip/arp"):
            if str(row.get("mac-address", "")).upper().replace("-", ":") == mac_n:
                ip = row.get("address")
                if ip:
                    return str(ip)
    except Exception:  # noqa: BLE001
        pass
    return None


def _kill_connections_for_ip(api: Any, ip: str) -> int:
    """
    Drop tracked/fasttracked connections so block takes effect immediately.
    YouTube buffers may still play a few seconds from local cache.
    """
    removed = 0
    path = api.path("/ip/firewall/connection")
    ids: list[str] = []
    try:
        for row in path:
            src = str(row.get("src-address") or "").split(":")[0]
            dst = str(row.get("dst-address") or "").split(":")[0]
            reply_src = str(row.get("reply-src-address") or "").split(":")[0]
            reply_dst = str(row.get("reply-dst-address") or "").split(":")[0]
            if ip in {src, dst, reply_src, reply_dst}:
                ids.append(row[".id"])
    except Exception:  # noqa: BLE001
        logger.warning("Connection list failed", exc_info=True)
        return 0

    for rid in ids:
        try:
            path.remove(rid)
            removed += 1
        except TrapError:
            pass
    if removed:
        logger.info("Killed %s connections for %s", removed, ip)
    return removed


def _find_id_by_comment(api: Any, comment: str) -> str | None:
    for row in api.path("/ip/firewall/filter"):
        if row.get("comment") == comment:
            return row.get(".id")
    return None


def _create_rule(api: Any, mac: str, comment: str, blocked: bool) -> str:
    path = api.path("/ip/firewall/filter")
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
    return rule_id


def ensure_device_rule(
    list_name: str,
    mac: str,
    *,
    blocked: bool = False,
    existing_rule_id: str | None = None,
) -> str:
    """
    Permanent forward drop for MAC. Enable when blocked, disable when allowed.
    On block: also kill active connections (fasttrack/YouTube) for instant cut.
    """
    mac = normalize_mac(mac)
    comment = rule_comment(list_name, mac)

    with mikrotik_api() as api:
        rule_id = existing_rule_id

        if rule_id:
            try:
                _set_disabled(api, rule_id, disabled=not blocked)
            except TrapError:
                logger.warning("Stored rule id %s missing, recreating", rule_id)
                rule_id = None

        if not rule_id:
            rule_id = _find_id_by_comment(api, comment)
            if rule_id:
                _set_disabled(api, rule_id, disabled=not blocked)
            else:
                rule_id = _create_rule(api, mac, comment, blocked)
                logger.info("Created permanent rule %s for %s", rule_id, mac)

        # Keep rule above established/fasttrack so drop actually hits live streams
        if blocked:
            place_before = _resolve_place_before(api)
            _move_before(api, rule_id, place_before)
            ip = _lookup_ip_for_mac(api, mac)
            if ip:
                _kill_connections_for_ip(api, ip)

        return rule_id


def set_internet_blocked(
    list_name: str,
    mac: str,
    blocked: bool,
    *,
    existing_rule_id: str | None = None,
) -> str:
    return ensure_device_rule(
        list_name,
        mac,
        blocked=blocked,
        existing_rule_id=existing_rule_id,
    )


def lookup_ip_for_mac(mac: str) -> str | None:
    """Public helper: current IP for MAC from DHCP/ARP."""
    with mikrotik_api() as api:
        return _lookup_ip_for_mac(api, mac)


def kill_connections_for_ip(ip: str) -> int:
    """Public: drop tracked connections for an IP (instant cut for open streams)."""
    with mikrotik_api() as api:
        return _kill_connections_for_ip(api, ip)


def remove_device_rule(rule_id: str | None, list_name: str, mac: str) -> None:
    if not is_configured():
        return
    comment = rule_comment(list_name, mac)
    with mikrotik_api() as api:
        path = api.path("/ip/firewall/filter")
        target = rule_id
        if not target:
            target = _find_id_by_comment(api, comment)
        if target:
            try:
                path.remove(target)
            except TrapError:
                pass


def ensure_firewall_drop_rule(list_name: str) -> str:
    return f"ok:{list_name}"


def _traffic_comment(mac: str) -> str:
    return f"internet-manager-traffic:{normalize_mac(mac)}"


def _mark_name(mac: str) -> str:
    return "im" + normalize_mac(mac).replace(":", "").lower()


def _ensure_fasttrack_skips_marked(api: Any) -> None:
    """
    Default defconf fasttrack bypasses queues. Limit it to unmarked connections
    so our per-MAC accounting queues actually count traffic.
    """
    path = api.path("/ip/firewall/filter")
    for row in path:
        if row.get("action") != "fasttrack-connection":
            continue
        if str(row.get("connection-mark") or "") == "no-mark":
            return
        try:
            path.update(**{".id": row[".id"], "connection-mark": "no-mark"})
            logger.info("Updated fasttrack rule %s to connection-mark=no-mark", row[".id"])
        except TrapError as exc:
            logger.warning("Could not update fasttrack for accounting: %s", exc)
        return


def ensure_traffic_accounting(mac: str, existing_queue_id: str | None = None) -> str:
    """
    Create mangle marks + simple queue for per-MAC upload/download counters.
    Returns queue .id.
    """
    mac = normalize_mac(mac)
    comment = _traffic_comment(mac)
    mark = _mark_name(mac)

    with mikrotik_api() as api:
        _ensure_fasttrack_skips_marked(api)
        mangle = api.path("/ip/firewall/mangle")
        queues = api.path("/queue/simple")

        has_conn = False
        has_pkt = False
        for row in mangle:
            if row.get("comment") == comment and row.get("action") == "mark-connection":
                has_conn = True
            if row.get("comment") == comment and row.get("action") == "mark-packet":
                has_pkt = True

        if not has_conn:
            try:
                mangle.add(
                    chain="forward",
                    action="mark-connection",
                    **{"new-connection-mark": mark, "src-mac-address": mac, "passthrough": "yes"},
                    comment=comment,
                )
            except TrapError as exc:
                logger.warning("mangle mark-connection failed: %s", exc)

        if not has_pkt:
            try:
                mangle.add(
                    chain="forward",
                    action="mark-packet",
                    **{
                        "new-packet-mark": mark,
                        "connection-mark": mark,
                        "passthrough": "yes",
                    },
                    comment=comment,
                )
            except TrapError as exc:
                logger.warning("mangle mark-packet failed: %s", exc)

        queue_id = existing_queue_id
        if queue_id:
            found = False
            for row in queues:
                if row.get(".id") == queue_id:
                    found = True
                    break
            if not found:
                queue_id = None

        if not queue_id:
            for row in queues:
                if row.get("comment") == comment or row.get("name") == f"im-traffic-{mark}":
                    queue_id = row.get(".id")
                    break

        if not queue_id:
            try:
                ret = queues.add(
                    name=f"im-traffic-{mark}"[:60],
                    **{"packet-marks": mark},
                    target="",
                    comment=comment,
                )
            except TrapError:
                # older ROS may require target
                ret = queues.add(
                    name=f"im-traffic-{mark}"[:60],
                    **{"packet-marks": mark},
                    target="0.0.0.0/0",
                    comment=comment,
                )
            if isinstance(ret, str):
                queue_id = ret
            elif isinstance(ret, dict) and ret.get("ret"):
                queue_id = str(ret["ret"])
            else:
                for row in queues:
                    if row.get("comment") == comment:
                        queue_id = row.get(".id")
                        break
            if not queue_id:
                raise RuntimeError("Traffic queue sa nepodarilo vytvoriť")
            logger.info("Created traffic queue %s for %s", queue_id, mac)

        return queue_id


def _parse_queue_bytes(raw: Any) -> tuple[int, int]:
    """
    MikroTik simple queue bytes are usually 'upload/download'.
    Upload = from client, download = to client.
    """
    if raw is None:
        return 0, 0
    text = str(raw)
    if "/" in text:
        left, right = text.split("/", 1)
        try:
            return int(left), int(right)
        except ValueError:
            return 0, 0
    try:
        return 0, int(text)
    except ValueError:
        return 0, 0


def get_traffic_by_mac(macs: list[str]) -> dict[str, dict[str, int]]:
    """
    Returns {MAC: {upload_bytes, download_bytes}} for known accounting queues.
    """
    wanted = {normalize_mac(m) for m in macs}
    out: dict[str, dict[str, int]] = {m: {"upload_bytes": 0, "download_bytes": 0} for m in wanted}
    if not is_configured() or not wanted:
        return out

    with mikrotik_api() as api:
        for row in api.path("/queue/simple"):
            comment = str(row.get("comment") or "")
            if not comment.startswith("internet-manager-traffic:"):
                continue
            mac = comment.split(":", 1)[-1].upper()
            # comment format internet-manager-traffic:AA:BB:...
            mac = comment.replace("internet-manager-traffic:", "", 1).upper()
            if mac not in wanted:
                continue
            # Prefer dedicated stats print fields when present
            upload, download = _parse_queue_bytes(row.get("bytes"))
            if "bytes" not in row:
                # some ROS expose separately
                try:
                    upload = int(row.get("uploaded") or row.get("bytes-out") or upload)
                    download = int(row.get("downloaded") or row.get("bytes-in") or download)
                except (TypeError, ValueError):
                    pass
            out[mac] = {"upload_bytes": upload, "download_bytes": download}
    return out


def reset_traffic_counters(mac: str) -> None:
    mac = normalize_mac(mac)
    comment = _traffic_comment(mac)
    with mikrotik_api() as api:
        queues = api.path("/queue/simple")
        for row in queues:
            if row.get("comment") == comment:
                try:
                    queues.reset(**{".id": row[".id"]})
                except Exception:  # noqa: BLE001
                    try:
                        api("/queue/simple/reset-counters", **{".id": row[".id"]})
                    except Exception:  # noqa: BLE001
                        api("/queue/simple/reset", **{"numbers": row[".id"]})
                return


def remove_traffic_accounting(mac: str) -> None:
    if not is_configured():
        return
    mac = normalize_mac(mac)
    comment = _traffic_comment(mac)
    with mikrotik_api() as api:
        mangle = api.path("/ip/firewall/mangle")
        for row in list(mangle):
            if row.get("comment") == comment:
                try:
                    mangle.remove(row[".id"])
                except TrapError:
                    pass
        queues = api.path("/queue/simple")
        for row in list(queues):
            if row.get("comment") == comment:
                try:
                    queues.remove(row[".id"])
                except TrapError:
                    pass
