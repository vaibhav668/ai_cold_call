from fastapi import status

class AppException(Exception):
    """Base exception for all application errors."""
    def __init__(self, message: str, status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)

class DatabaseException(AppException):
    """Raised when a database operation fails."""
    def __init__(self, message: str):
        super().__init__(message, status.HTTP_500_INTERNAL_SERVER_ERROR)

class InvalidDatabaseURLError(DatabaseException):
    """Raised when the database connection URL is malformed or invalid."""
    pass

class DatabaseDNSResolveError(DatabaseException):
    """Raised when database hostname DNS resolution fails."""
    pass

class DatabasePortUnreachableError(DatabaseException):
    """Raised when database host TCP port is unreachable."""
    pass

class DatabaseSSLHandshakeError(DatabaseException):
    """Raised when database SSL negotiation fails."""
    pass

class DatabaseAuthenticationError(DatabaseException):
    """Raised when database credentials authentication fails."""
    pass

class DatabaseTimeoutError(DatabaseException):
    """Raised when connection to database times out."""
    pass

class DatabaseConnectionPoolError(DatabaseException):
    """Raised when database connection pool limit or check fails."""
    pass

class NotFoundException(AppException):
    """Raised when a requested resource is not found."""
    def __init__(self, message: str = "Resource not found"):
        super().__init__(message, status.HTTP_404_NOT_FOUND)

class ExternalAPIException(AppException):
    """Raised when an external API integration call fails (e.g. Plivo, Qdrant)."""
    def __init__(self, message: str, status_code: int = status.HTTP_502_BAD_GATEWAY):
        super().__init__(message, status_code)
