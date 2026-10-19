from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.responses import JSONResponse


def not_found() -> HTTPException:
    return HTTPException(404, {"error": "NOT_FOUND"})


def forbidden(perm: str) -> HTTPException:
    return HTTPException(403, {"error": "FORBIDDEN", "required": perm})


def conflict(code: str) -> HTTPException:
    return HTTPException(409, {"error": code})


# Codes for errors raised without a structured detail (routing 404/405 etc.). The
# framework's detail text is never echoed.
STATUS_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "VALIDATION_FAILED",
}


def install_error_handlers(app: FastAPI) -> None:
    # Starlette's HTTPException is FastAPI's base class, so this also covers routing 404/405.
    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        body = (
            exc.detail
            if isinstance(exc.detail, dict)
            else {"error": STATUS_CODES.get(exc.status_code, "HTTP_ERROR")}
        )
        return JSONResponse(body, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        return JSONResponse(
            {"error": "VALIDATION_FAILED", "fields": fields}, status_code=422
        )
