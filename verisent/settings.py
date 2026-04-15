import urllib.parse
from typing import Optional
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    # AI
    api_key: SecretStr
    gemini_api_key: SecretStr

    # Clerk
    clerk_secret_key: SecretStr
    clerk_publishable_key: str

    # Database
    db_pool_size: int = 20
    db_connection_string: Optional[SecretStr] = None
    db_user: SecretStr
    db_database: str
    db_password: SecretStr
    db_host: str
    db_port: str = "5432"

    # Blob storage
    blob_storage_connection_string: SecretStr
    blob_storage_container_name: str

    # RabbitMQ
    rabbitmq_url: SecretStr
    rabbitmq_queue_name: str 
    
    # App
    app_url: str = "http://localhost:5173"

    # Brevo
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: SecretStr
    smtp_from: str

    @property
    def db_conn_str(self) -> str:
        if self.db_connection_string is not None:
            return self.db_connection_string.get_secret_value()
        user = urllib.parse.quote(self.db_user.get_secret_value())
        password = urllib.parse.quote(self.db_password.get_secret_value())
        return f"postgresql://{user}:{password}@{self.db_host}:{self.db_port}/{self.db_database}"

    @property
    def async_db_conn_str(self) -> str:
        return self.db_conn_str.replace("postgresql://", "postgresql+asyncpg://")

    @property
    def sync_db_conn_str(self) -> str:
        return self.db_conn_str.replace("postgresql://", "postgresql+psycopg2://")


settings = AppSettings() # type: ignore