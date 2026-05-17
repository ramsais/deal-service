from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    STORAGE_FILE_PATH: str = "app/storage/deals.json"
    LOG_LEVEL: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
