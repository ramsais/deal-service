from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    STORAGE_FILE_PATH: str = "app/storage/deals.json"
    LOG_LEVEL: str = "INFO"
    COMPANY_SERVICE_URL: str = "http://company.dev.svc.local:8000"
    COMPANY_SERVICE_TIMEOUT: float = 5.0

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
