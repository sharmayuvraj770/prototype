"""Northstar MCP server: low-level SDK `Server` + our own handlers, behind bearer auth.

Stateless Streamable HTTP with JSON responses. Identity comes only from the verified
token (`identity.current_identity`); tool listing and calls are our deterministic code.
"""
from typing import Any

from mcp import types
from mcp.server.auth.settings import AuthSettings
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from mcp_server.identity import JwtTokenVerifier
from mcp_server.registry import Registry
from mcp_server.settings import Settings

INSTRUCTIONS = (
    "Northstar TMS tools for freight-brokerage staff. Answer only from tool results, cite "
    "load/carrier IDs and as_of, and say when something can't be found instead of guessing. "
    "Tools are filtered by the signed-in user's role; financial actions require human approval."
)


def tool_error(code: str, message: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(text=f"{code}: {message}")],
        structured_content={"error": code, "message": message},
        is_error=True,
    )


def create_app(settings: Settings | None = None, *, registry: Registry | None = None) -> Starlette:
    settings = settings or Settings()  # type: ignore[call-arg]
    registry = registry or Registry()

    async def list_tools(
        ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[])  # role-filtered registry arrives in Task 5

    async def call_tool(
        ctx: ServerRequestContext[Any], params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        return tool_error(
            "TOOL_NOT_AVAILABLE",
            "That tool is not available to you. Call tools/list and use only the tools listed.",
        )

    server = Server(
        "northstar", instructions=INSTRUCTIONS, on_list_tools=list_tools, on_call_tool=call_tool
    )

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    verifier = JwtTokenVerifier(
        settings.jwt_public_key_path.read_bytes(),
        audience=settings.mcp_audience,
        issuer=settings.jwt_issuer,
    )
    app = server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        auth=AuthSettings(
            issuer_url=settings.jwt_issuer,
            resource_server_url=settings.resource_server_url,
            required_scopes=[],
            validate_token_resource=False,  # JwtTokenVerifier checks aud itself
        ),
        token_verifier=verifier,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.allowed_hosts,
            allowed_origins=[f"http://{h}" for h in settings.allowed_hosts],
        ),
        custom_starlette_routes=[Route("/health", health, methods=["GET"])],
    )
    app.state.settings = settings
    app.state.registry = registry
    return app
