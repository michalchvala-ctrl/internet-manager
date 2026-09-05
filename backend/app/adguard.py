"""AdGuard Home API – soft-block social domains per client (by MAC/IP)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

# Default social domains if DB is empty
DEFAULT_SOCIAL_DOMAINS = [
    "tiktok.com",
    "www.tiktok.com",
    "musical.ly",
    "snapchat.com",
    "www.snapchat.com",
    "instagram.com",
    "www.instagram.com",
    "cdninstagram.com",
    "facebook.com",
    "www.facebook.com",
    "fbcdn.net",
    "messenger.com",
    "discord.com",
    "discordapp.com",
    "youtube.com",
    "www.youtube.com",
    "youtu.be",
    "m.youtube.com",
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
            return True, f"OK (v{version})"
    except Exception as exc:  # noqa: BLE001
        logger.exception("AdGuard ping failed")
        return False, str(exc)


def _rules_for_domains(domains: list[str]) -> list[str]:
    rules: list[str] = []
    for d in domains:
        d = d.strip().lower().lstrip(".")
        if not d:
            continue
        rules.append(f"||{d}^")
    return rules


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
        # also match by name tag
        if c.get("name") == f"im-{mac_n}":
            return c
    return None


def set_social_blocked(mac: str, blocked: bool, domains: list[str] | None = None) -> None:
    """
    Create/update an AdGuard client for the MAC with per-client blocked hosts.
    When blocked=False, clear blocked_hosts (or remove client rules).
    """
    mac_n = mac.upper().replace("-", ":")
    domains = domains or DEFAULT_SOCIAL_DOMAINS
    rules = _rules_for_domains(domains) if blocked else []

    existing = find_client_by_mac(mac_n)
    name = existing["name"] if existing else f"im-{mac_n}"

    payload: dict[str, Any] = {
        "name": name,
        "ids": list({*(existing.get("ids") if existing else []), mac_n}),
        "use_global_settings": True,
        "filtering_enabled": True,
        "safebrowsing_enabled": False,
        "parental_enabled": False,
        "safesearch_enabled": False,
        "use_global_blocked_services": True,
        "blocked_services": [],
        "upstreams": [],
        "tags": ["user_child"] if blocked else [],
        "blocked_hosts": [d.strip().lower().lstrip(".") for d in domains] if blocked else [],
        "ignore_querylog": False,
        "ignore_statistics": False,
    }

    # Prefer AdGuard blocked_services for social if available
    if blocked:
        payload["use_global_blocked_services"] = False
        payload["blocked_services"] = [
            "tiktok",
            "snapchat",
            "instagram",
            "facebook",
            "discord",
            "youtube",
            "twitch",
            "twitter",
            "reddit",
        ]

    with _client() as client:
        if existing:
            body = {"name": existing["name"], "data": payload}
            r = client.post("/control/clients/update", json=body)
            if r.status_code >= 400:
                # fallback: some versions want different shape
                r2 = client.post(
                    "/control/clients/update",
                    json={"name": existing["name"], "data": {**payload, "name": existing["name"]}},
                )
                r2.raise_for_status()
            else:
                r.raise_for_status()
        else:
            r = client.post("/control/clients/add", json=payload)
            r.raise_for_status()

    # Also keep a named custom filter list for soft global mode (unused per-device)
    _ = rules
