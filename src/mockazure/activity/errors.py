from starlette.responses import JSONResponse


class ActivityError(Exception):
    def __init__(self, status, code, message, headers=None):
        self.status, self.code, self.message, self.headers = status, code, message, headers or {}


def response(error):
    return JSONResponse(
        {"error": {"code": error.code, "message": error.message}},
        status_code=error.status,
        headers=error.headers,
    )


async def handle(request, error):
    return response(error)
