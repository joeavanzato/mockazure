"""Key Vault JSON errors never include supplied values or bearer credentials."""


class VaultError(Exception):
    def __init__(self, status, code, message, headers=None):
        self.status, self.code, self.message = status, code, message
        self.headers = headers or {}

    def body(self):
        return {"error": {"code": self.code, "message": self.message}}
