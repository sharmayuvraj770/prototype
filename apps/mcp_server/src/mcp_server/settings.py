from pathlib import Path

from pydantic_settings import BaseSettings

APP_ROOT = Path(__file__).resolve().parents[2]  # apps/mcp_server


class Settings(BaseSettings):
    api_base_url: str = "http://mock_tms_api:8001"
    redis_url: str = "redis://redis:6379/0"
    database_url: str
    jwt_public_key_path: Path = Path("/keys/jwt_public.pem")
    jwt_private_key_path: Path = Path("/keys/jwt_private.pem")
    audit_key_path: Path = Path("/keys/audit.key")
    jwt_issuer: str = "http://mock-idp:9000"
    mcp_audience: str = "northstar-mcp"
    api_audience: str = "northstar-public-api"
    resource_server_url: str = "http://localhost:8000/mcp"
    manifest_path: Path = APP_ROOT / "mcp_manifest.yaml"
    allowed_hosts: list[str] = ["localhost:*", "127.0.0.1:*", "mcp_server:*"]
    log_dir: Path = Path("logs")
