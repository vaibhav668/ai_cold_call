import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from app.core.logging import request_id_var, logger

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        # Generate or capture correlation ID
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        
        # Set context variable
        token = request_id_var.set(request_id)
        
        logger.debug(f"Handling request: {request.method} {request.url.path}")
        
        try:
            response: Response = await call_next(request)
            # Add request ID to response header
            response.headers["X-Request-ID"] = request_id
            return response
        except Exception as e:
            logger.exception(f"Unhandled exception during request: {e}")
            raise e
        finally:
            # Reset context variable to avoid leak
            request_id_var.reset(token)
