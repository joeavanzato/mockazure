"""Logs error envelope; no Graph or ARM error wrapper leakage."""

from starlette.responses import JSONResponse


class LogsError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)

    def body(self):
        return {"error": {"code": self.code, "message": self.message}}


def response(error, headers=None):
    return JSONResponse(error.body(), status_code=error.status, headers=headers)
