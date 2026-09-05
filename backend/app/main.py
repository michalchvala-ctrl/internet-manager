import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import adguard
from app.auth import get_user_by_username, hash_password
from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.models import SocialDomain, User
from app.routers import auth, devices

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()
app = FastAPI(title="Internet Manager", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(devices.router)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def seed_db() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if not get_user_by_username(db, settings.admin_username):
            admin = User(
                username=settings.admin_username,
                password_hash=hash_password(settings.admin_password),
                is_admin=True,
                is_active=True,
            )
            db.add(admin)
            logger.info("Created default admin user: %s", settings.admin_username)

        if db.query(SocialDomain).count() == 0:
            for domain in adguard.DEFAULT_SOCIAL_DOMAINS:
                db.add(SocialDomain(domain=domain, enabled=True))
            logger.info("Seeded default social domains")

        db.commit()
    finally:
        db.close()


@app.on_event("startup")
def on_startup() -> None:
    # Ensure data dir exists for sqlite
    if settings.database_url.startswith("sqlite:///"):
        db_path = settings.database_url.replace("sqlite:///", "", 1)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    seed_db()


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


if STATIC_DIR.is_dir():
    assets = STATIC_DIR / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str) -> FileResponse:
        candidate = STATIC_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
