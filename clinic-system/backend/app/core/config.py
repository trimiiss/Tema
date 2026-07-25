from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    openai_api_key: str
    supabase_url: str
    supabase_service_role_key: str
    supabase_anon_key: str
    supabase_jwt_secret: str
    secret_key: str = "change-me"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
