from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "LoanHub"
    DEBUG: bool = False
    DATABASE_URL: str
    DB_SCHEMA: str = "loanhub"
    LOG_LEVEL: str = "INFO"
    POOL_SIZE: int = 5
    MAX_OVERFLOW: int = 10
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin1234"
    ADMIN_EMAIL: str = "admin@loanhub.com"

    # JWT settings
    JWT_SECRET_KEY: str = "change-me-in-production-use-a-long-random-string"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()
