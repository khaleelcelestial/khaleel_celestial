from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    app_name: str = Field(default="TaskManagementAPI", env="APP_NAME")
    app_version: str = Field(default="1.0.0", env="APP_VERSION")
    debug: bool = Field(default=False, env="DEBUG")
    log_level: str = Field(default="INFO", env="LOG_LEVEL")
    log_file: str = Field(default="logs/app.log", env="LOG_FILE")
    data_dir: str = Field(default="data", env="DATA_DIR")
    tasks_file: str = Field(default="data/tasks.json", env="TASKS_FILE")
    users_file: str = Field(default="data/users.json", env="USERS_FILE")
    secret_key: str = Field(default="changeme", env="SECRET_KEY")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
