"""AdGuard Home API – soft-block social networks per device."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

DEFAULT_SOCIAL_DOMAINS = [
    "tiktok.com",
    "www.tiktok.com",
    "musical.ly",
    "snapchat.com",
    "www.snapchat.com",
    "instagram.com",
    "www.instagram.com",
    "cdninstagram.com",
    "scontent.cdninstagram.com",
    "ig.me",
    "facebook.com",
    "www.facebook.com",
    "m.facebook.com",
    "fbcdn.net",
    "facebook.net",
    "fb.com",
    "messenger.com",
    "meta.com",
    "discord.com",
    "discordapp.com",
    "discord.gg",
    "youtube.com",
    "www.youtube.com",
    "youtu.be",
    "m.youtube.com",
    "googlevideo.com",
    "ytimg.com",
    "reddit.com",
    "redd.it",
    "twitch.tv",
    "twitter.com",
    "x.com",
    "t.co",
    "whatsapp.com",
    "whatsapp.net",
]

SOCIAL_SERVICE_IDS = [
    "tiktok",
    "snapchat",
    "instagram",
    "facebook",
    "discord",
    "youtube",
    "twitch",
    "twitter",
    "reddit",
    "whatsapp",
]


def is_configured() -> bool:
    s = get_settings()
    return bool(s.adguard_url and s.adguard_user)


def _client() -> httpx.Client:
    s = get_settings()
    if not is_configured():
        raise RuntimeError("AdGuard nie je nakonfigurovaný (ADGUARD_URL / USER / PASSWORD)")
    return httpx.Client(
        base_url=s.adguard_url.rstrip("/"),
        auth=(s.adguard_user, s.adguard_password),
        timeout=15.0,
    )


def ping() -> tuple[bool, str | None]:
    if not is_configured():
        return False, "Nie je nakonfigurovaný"
    try:
        with _client() as client:
            r = client.get("/control/status")
            r.raise_for_status()
            data = r.json()
            version = data.get("version", "?")
            protection = data.get("protection_enabled")
            extra = "" if protection else " | OCHRANA VYPNUTÁ"
            return True, f"OK (v{version}){extra}"
    except Exception as exc:  # noqa: BLE001
        logger.exception("AdGuard ping failed")
        return False, str(exc)


def ensure_protection_on() -> None:
    """AdGuard Home ignores filters while global protection is off."""
    with _client() as client:
        r = client.get("/control/status")
        r.raise_for_status()
        if r.json().get("protection_enabled"):
            return
        # duration 0 = until manually disabled
        r2 = client.post("/control/protection", json={"enabled": True, "duration": 0})
        if r2.status_code >= 400:
            # older versions
            r3 = client.post("/control/enable")
            r3.raise_for_status()
        else:
            r2.raise_for_status()
        logger.info("AdGuard protection enabled via API")


def get_clients() -> list[dict[str, Any]]:
    with _client() as client:
        r = client.get("/control/clients")
        r.raise_for_status()
        data = r.json()
        return list(data.get("clients") or [])


def find_client_by_mac(mac: str) -> dict[str, Any] | None:
    mac_n = mac.upper().replace("-", ":")
    for c in get_clients():
        ids = [str(x).upper().replace("-", ":") for x in (c.get("ids") or [])]
        if mac_n in ids:
            return c
        if c.get("name") == f"im-{mac_n}":
            return c
    return None


def _blocked_services_payload(blocked: bool) -> Any:
    """
    AdGuard Home 0.107+ wants {"ids": [...], "schedule": {...}}.
    Older versions accept a plain string list.
    """
    if not blocked:
        # try modern empty shape first; callers may fall back
        return {"ids": [], "schedule": {"time_zone": "Local"}}
    return {
        "ids": SOCIAL_SERVICE_IDS,
        "schedule": {"time_zone": "Local"},
    }


def _client_payload(
    name: str,
    ids: list[str],
    blocked: bool,
    domains: list[str],
    *,
    modern_services: bool = True,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "ids": ids,
        "use_global_settings": False,
        "filtering_enabled": True,
        "safebrowsing_enabled": False,
        "parental_enabled": False,
        "safesearch_enabled": False,
        "use_global_blocked_services": not blocked,
        "upstreams": [],
        "tags": ["user_child"] if blocked else [],
        "blocked_hosts": [d.strip().lower().lstrip(".") for d in domains] if blocked else [],
        "ignore_querylog": False,
        "ignore_statistics": False,
    }
    if modern_services:
        payload["blocked_services"] = _blocked_services_payload(blocked)
    else:
        payload["blocked_services"] = SOCIAL_SERVICE_IDS if blocked else []
    return payload


def set_social_blocked(
    mac: str,
    blocked: bool,
    domains: list[str] | None = None,
    *,
    ip: str | None = None,
) -> None:
    """
    Create/update an AdGuard client for the device.
    Client ids should include MAC and current IP.
    DNS queries must come from that device IP (not only via router DNS proxy).
    """
    ensure_protection_on()

    mac_n = mac.upper().replace("-", ":")
    domains = domains or DEFAULT_SOCIAL_DOMAINS
    existing = find_client_by_mac(mac_n)
    name = existing["name"] if existing else f"im-{mac_n}"

    ids: list[str] = []
    if existing:
        ids.extend(str(x) for x in (existing.get("ids") or []))
    ids.append(mac_n)
    if ip:
        ids.append(ip)
    # unique preserve order
    seen: set[str] = set()
    uniq_ids: list[str] = []
    for i in ids:
        key = i.upper()
        if key in seen:
            continue
        seen.add(key)
        uniq_ids.append(i)

    with _client() as client:
        # Try modern blocked_services object, then legacy list
        last_error: Exception | None = None
        for modern in (True, False):
            payload = _client_payload(name, uniq_ids, blocked, domains, modern_services=modern)
            try:
                if existing:
                    r = client.post(
                        "/control/clients/update",
                        json={"name": existing["name"], "data": payload},
                    )
                    r.raise_for_status()
                else:
                    r = client.post("/control/clients/add", json=payload)
                    r.raise_for_status()
                    existing = {"name": name}
                last_error = None
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning("AdGuard client update failed (modern=%s): %s", modern, exc)

        if last_error:
            raise last_error

    logger.info(
        "AdGuard social blocked=%s client=%s ids=%s",
        blocked,
        name,
        uniq_ids,
    )
