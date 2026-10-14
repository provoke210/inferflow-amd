from pathlib import Path
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="INFERFLOW_", extra="ignore", hide_input_in_errors=True)
    mode: str = "fake"
    amd_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    base_url: str = "https://developer.amd.com.cn/radeon/api/v1"
    model: str = ""
    rpm: int = Field(default=120, ge=1, le=100000)
    concurrency: int = Field(default=3, ge=1, le=64)
    limits_confirmed: bool = False
    daily_budget: int = Field(default=10, ge=1, le=100000)
    queue_size: int = Field(default=64, ge=1, le=1000)
    cache_ttl: int = Field(default=3600, ge=1)
    db_path: str = "data/inferflow.sqlite3"

    @model_validator(mode="after")
    def validate_live(self):
        if self.mode not in {"fake", "amd"}:
            raise ValueError("mode must be fake or amd")
        if self.mode == "amd":
            if not self.amd_api_key.get_secret_value() or not self.limits_confirmed:
                raise ValueError("AMD mode requires a local key and confirmed account RPM/concurrency limits")
            if self.base_url.rstrip("/") != "https://developer.amd.com.cn/radeon/api/v1":
                raise ValueError("AMD mode only sends credentials to the verified AMD endpoint")
        return self
