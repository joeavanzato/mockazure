from datetime import UTC, datetime

from starlette.responses import JSONResponse


class GraphError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


def graph_error_response(request, error: GraphError):
    request_id = request.state.request_id
    headers = {"request-id": request_id}
    if error.status == 401:
        headers["WWW-Authenticate"] = 'Bearer error="invalid_token"'
    return JSONResponse(
        status_code=error.status,
        headers=headers,
        content={
            "error": {
                "code": error.code,
                "message": error.message,
                "innerError": {
                    "date": datetime.now(UTC).isoformat(),
                    "request-id": request_id,
                    "client-request-id": request_id,
                },
            }
        },
    )
