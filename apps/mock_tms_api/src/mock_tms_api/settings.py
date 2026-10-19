from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    jwt_public_key_path: Path = Path("/keys/jwt_public.pem")
    jwt_issuer: str = "http://mock-idp:9000"
    api_audience: str = "northstar-public-api"
