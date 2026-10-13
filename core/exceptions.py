from rest_framework import exceptions, status
from rest_framework.response import Response


def api_exception_handler(exc, context):
    """Render DRF exceptions in the contract's error shape."""
    if isinstance(exc, exceptions.ParseError):
        code, message = "VALIDATION_ERROR", "Request body must be valid JSON."
    elif isinstance(exc, exceptions.NotFound):
        code, message = "NOT_FOUND", "The requested resource was not found."
    elif isinstance(exc, exceptions.UnsupportedMediaType):
        code, message = "VALIDATION_ERROR", "Request content type must be JSON."
    elif isinstance(exc, exceptions.MethodNotAllowed):
        code, message = "METHOD_NOT_ALLOWED", "Method not allowed."
    elif isinstance(exc, exceptions.APIException):
        code, message = "VALIDATION_ERROR", str(exc.detail)
    else:
        return None
    return Response(
        {"error": {"code": code, "message": message, "details": []}},
        status=exc.status_code,
    )
