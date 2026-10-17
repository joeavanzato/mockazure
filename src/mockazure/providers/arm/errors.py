"""ARM error envelopes, separate from Graph OData error details."""

from starlette.responses import JSONResponse


class ArmError(Exception):
    def __init__(self, status: int, code: str, message: str, target: str | None = None):
        super().__init__(message)
        self.headers = {}
        self.status, self.code, self.message, self.target = status, code, message, target

    def body(self):
        error = {"code": self.code, "message": self.message}
        if self.target is not None:
            error["target"] = self.target
        return {"error": error}


async def handle_arm_error(request, error: ArmError):
    headers = dict(error.headers)
    if request_id := getattr(request.state, "request_id", None):
        headers["x-ms-request-id"] = str(request_id)
    return JSONResponse(error.body(), status_code=error.status, headers=headers)
