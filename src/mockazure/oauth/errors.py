from datetime import UTC, datetime

from starlette.responses import JSONResponse


class OAuthError(Exception):
    def __init__(self, error: str, description: str, status: int = 400):
        self.error, self.description, self.status = error, description, status
        super().__init__(description)


def oauth_error_response(request, error: OAuthError):
    response = JSONResponse(
        status_code=error.status,
        content={
            "error": error.error,
            "error_description": error.description,
            "error_codes": [],
            "timestamp": datetime.now(UTC).isoformat(),
            "trace_id": request.state.request_id,
            "correlation_id": request.state.request_id,
        },
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )
    if error.status == 401:
        response.headers["WWW-Authenticate"] = 'Bearer error="invalid_token"'
    return response
