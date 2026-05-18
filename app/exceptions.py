from fastapi import HTTPException


class AppException(HTTPException):
    def __init__(self, status_code: int, error: str, message: str, details: dict | None = None):
        super().__init__(status_code=status_code, detail={"error": error, "message": message, "details": details or {}})
        self.error = error
        self.message = message
        self.details = details or {}


class ResourceNotFoundException(AppException):
    def __init__(self, message: str = "Resource not found", details: dict | None = None):
        super().__init__(status_code=404, error="RESOURCE_NOT_FOUND", message=message, details=details)


class StorageException(AppException):
    def __init__(self, message: str = "Storage error occurred", details: dict | None = None):
        super().__init__(status_code=500, error="STORAGE_ERROR", message=message, details=details)


class CompanyServiceException(AppException):
    def __init__(self, message: str = "Company service error occurred", details: dict | None = None):
        super().__init__(status_code=502, error="COMPANY_SERVICE_ERROR", message=message, details=details)
