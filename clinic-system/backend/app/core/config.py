from pydantic_settings import BaseSettings


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
    # Fallback working hours, used when a doctor has no `schedules` rows at all.
    # Once a doctor has any row, only their own rows apply. 0=Mon … 6=Sun.
    default_work_days: str = "0,1,2,3,4"
    default_work_start: str = "09:00"
    default_work_end: str = "17:00"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
