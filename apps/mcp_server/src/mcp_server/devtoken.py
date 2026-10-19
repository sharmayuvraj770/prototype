"""Print an MCP-audience dev token for a seeded user.

    uv run python -m mcp_server.devtoken a.ops [--client-id dev-cli]
"""
import argparse
import sys

from northstar_shared.tokens import mint_token
from northstar_shared.users import tenant_role_for

from mcp_server.settings import Settings


def make_token(user_id: str, *, client_id: str, settings: Settings, ttl_s: int = 3600) -> str:
    tenant, role = tenant_role_for(user_id)
    return mint_token(
        settings.jwt_private_key_path.read_bytes(), sub=user_id, tenant=tenant, role=role,
        aud=settings.mcp_audience, iss=settings.jwt_issuer, ttl_s=ttl_s, client_id=client_id,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m mcp_server.devtoken", description=__doc__)
    p.add_argument("user_id", help="seeded user id, e.g. a.ops or b.manager")
    p.add_argument("--client-id", default="dev-cli")
    p.add_argument("--ttl", type=int, default=3600, help="lifetime in seconds")
    args = p.parse_args(argv)
    try:
        settings = Settings(database_url="unused")  # devtoken never touches the DB
        print(make_token(args.user_id, client_id=args.client_id, settings=settings,
                         ttl_s=args.ttl))
    except (ValueError, OSError) as e:
        print(f"devtoken: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
