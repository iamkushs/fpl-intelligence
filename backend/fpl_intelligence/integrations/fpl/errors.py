"""Explicit failures raised by the Official FPL boundary."""


class OfficialFPLError(RuntimeError):
    """Base class for failures fetching or interpreting official FPL data."""

    code = "OFFICIAL_FPL_ERROR"

    def __init__(self, message: str, *, cause: Exception | None = None):
        super().__init__(message)
        self.cause = cause


class OfficialFPLTransportError(OfficialFPLError):
    code = "OFFICIAL_FPL_UNAVAILABLE"


class OfficialFPLHTTPError(OfficialFPLError):
    code = "OFFICIAL_FPL_HTTP_ERROR"

    def __init__(self, status_code: int, message: str | None = None):
        self.status_code = status_code
        super().__init__(message or f"Official FPL returned HTTP {status_code}")


class OfficialFPLSchemaError(OfficialFPLError):
    code = "OFFICIAL_FPL_INVALID_RESPONSE"
