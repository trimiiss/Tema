from pathlib import Path

from pydantic_settings import BaseSettings

# backend/ — this file is backend/app/core/config.py
BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    openai_api_key: str
    supabase_url: str
    supabase_service_role_key: str
    supabase_anon_key: str
    supabase_jwt_secret: str
    secret_key: str = "change-me"
    # Wall-clock timezone the clinic operates in. Working hours ("09:00–17:00")
    # and generated slots are interpreted in this zone; `appointments.scheduled_at`
    # is TIMESTAMPTZ, so every datetime crossing the DB boundary must be aware.
    # The IANA database has no Europe/Pristina entry; Europe/Tirane is a
    # standalone zone carrying exactly the offsets and EU DST rules Kosovo
    # observes (CET/CEST, UTC+1 winter / UTC+2 summer).
    clinic_timezone: str = "Europe/Tirane"
    # Where uploaded documents are written. A relative path resolves against the
    # backend package root (see `upload_path`), which is `/app` in the container
    # because compose mounts `./backend` there, and `backend/` when running
    # uvicorn locally — so this default is correct in both. It used to be the
    # absolute `/app/uploads`, which on Windows silently became `C:\app\uploads`.
    upload_dir: str = "uploads"
    # Fallback working hours, used when a doctor has no `schedules` rows at all.
    # Once a doctor has any row, only their own rows apply. 0=Mon … 6=Sun.
    default_work_days: str = "0,1,2,3,4"
    default_work_start: str = "09:00"
    default_work_end: str = "17:00"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()


def upload_path() -> Path:
    """Absolute directory for uploaded documents."""
    configured = Path(settings.upload_dir)
    return configured if configured.is_absolute() else BACKEND_ROOT / configured
