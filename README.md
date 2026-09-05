# Internet Manager

Jednoduchá mobile-first webovka na zapínanie/vypínanie **internetu** a **sociálnych sietí** po zariadeniach (MAC). Beží v Dockeri na tvojom serveri.

- **Internet OFF** → MikroTik `address-list` + firewall `drop` (Wi‑Fi ostáva, LAN/HA funguje)
- **Sociálne OFF** → AdGuard Home client rules (TikTok, IG, Snap, …)
- Prihlásenie menom/heslom, admin vie vytvárať používateľov a zariadenia

## Unraid (Add Container) – odporúčané

Appka sa **nestahuje zo zdrojov pri každom update**. Flow je:

1. Kód na GitHub → GitHub Actions postaví Docker image → uloží ho do **GHCR** (`ghcr.io/...`)
2. Unraid Add Container → Repository = ten image
3. Update = Unraid „Check for Updates“ / pull nového `latest`

### Image

Po pushi do `main` vznikne (názov podľa repa):

```text
ghcr.io/michalchvala-ctrl/internet-manager:latest
```

Ak je package private: v GitHub → Packages → internet-manager → Package settings → **Change visibility → Public**  
(alebo na Unraide `docker login ghcr.io` s Personal Access Token s `read:packages`).

### Add Container polia

| Pole | Hodnota |
|------|---------|
| Name | `internet-manager` |
| Repository | `ghcr.io/michalchvala-ctrl/internet-manager:latest` |
| Network Type | `Bridge` (alebo tvoja LAN custom network) |
| Port | Host `8088` → Container `8000` (TCP) |
| Path | Host `/mnt/user/appdata/internet-manager` → Container `/data` |
| Restart | `unless-stopped` |

**Variables** (Environment):

| Key | Example |
|-----|---------|
| `SECRET_KEY` | dlhý náhodný reťazec |
| `ADMIN_USERNAME` | `admin` |
| `ADMIN_PASSWORD` | silné heslo |
| `DATABASE_URL` | `sqlite:////data/internet_manager.db` |
| `MIKROTIK_HOST` | IP RB4011 |
| `MIKROTIK_USER` | API user |
| `MIKROTIK_PASSWORD` | … |
| `MIKROTIK_PORT` | `8728` |
| `ADGUARD_URL` | `http://192.168.x.x:3000` |
| `ADGUARD_USER` | … |
| `ADGUARD_PASSWORD` | … |

Update: Docker → internet-manager → **force update** / pull latest, alebo CA Auto Update.

## Rýchly štart (docker compose)

```bash
cp .env.example .env
# uprav .env – SECRET_KEY, heslá, MikroTik, AdGuard

docker compose up -d --build
```

Otvor `http://SERVER:8088`.

Predvolený admin: `ADMIN_USERNAME` / `ADMIN_PASSWORD` (vytvorí sa pri prvom štarte).
## MikroTik (raz nastaviť)

1. Vytvor API používateľa (len práva na firewall address-list / filter, ideálne nie full admin).
2. Povoľ API na porte `8728` (alebo SSL `8729`).
3. Firewall pravidlo sa vie vytvoriť automaticky pri pridaní zariadenia, alebo ručne:

```
/ip firewall filter
add chain=forward action=drop src-address-list=kids-anicka \
    comment="internet-manager-drop:kids-anicka" place-before=0
```

Pri pridaní zariadenia v UI zadaj:
- **Názov** – Anička
- **MAC** – telefónu
- **Address-list** – `kids-anicka` (musí sedieť s firewall pravidlom)

Prepínač **Internet OFF** pridá MAC do listu → drop. **ON** MAC z listu odoberie.

## AdGuard (sociálne)

1. AdGuard Home musí vidieť DNS traffic zariadení (DHCP → AdGuard, alebo redirect DNS).
2. Vyplň `ADGUARD_*` v `.env`.
3. Prepínač **Sociálne OFF** vytvorí/upraví AdGuard klienta podľa MAC a zapne blocked services (tiktok, instagram, …).

Ak AdGuard nie je nastavený, Internet ON/OFF stále funguje cez MikroTik.

## Lokálny vývoj

```bash
# backend
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
set DATABASE_URL=sqlite:///./dev.db
uvicorn app.main:app --reload --port 8000

# frontend (druhý terminál)
cd frontend
npm install
npm run dev
```

UI: `http://127.0.0.1:5173` (proxy na API).

## Štruktúra

```
backend/app/     FastAPI + SQLite + MikroTik/AdGuard klienti
frontend/        React (Vite) – prepínače, zariadenia, používatelia
Dockerfile       multi-stage build (UI + API v jednom kontajneri)
docker-compose.yml
```

## Poznámky k sieti

- Kontajner musí vedieť dosiahnuť MikroTik API a AdGuard URL (nie `localhost` routera, ale jeho LAN IP).
- Na Linuxe môžeš použiť `network_mode: host` v compose, ak máš routing problémy.
- Odporúčané: webovku nepublikovať do internetu – len LAN / VPN / reverse proxy s auth.
