import logging
import sys
from contextvars import ContextVar
from typing import Any, Dict
try:
    from pythonjsonlogger import json as json_formatter_module
except ImportError:
    from pythonjsonlogger import jsonlogger as json_formatter_module
from app.core.config import settings

# Global ContextVar to store request/correlation ID
request_id_var: ContextVar[str] = ContextVar("request_id", default="")

class CustomJsonFormatter(json_formatter_module.JsonFormatter):
    def add_fields(self, log_record: Dict[str, Any], record: logging.LogRecord, message_dict: Dict[str, Any]) -> None:
        super().add_fields(log_record, record, message_dict)
        # Add timestamp if not present
        if not log_record.get("timestamp"):
            import datetime
            log_record["timestamp"] = datetime.datetime.utcnow().isoformat() + "Z"
        
        # Add log level
        if log_record.get("level"):
            log_record["level"] = log_record["level"].upper()
        else:
            log_record["level"] = record.levelname
            
        # Add tracing request ID from ContextVar
        req_id = request_id_var.get()
        if req_id:
            log_record["request_id"] = req_id

def setup_logging() -> None:
    # Get root logger
    root_logger = logging.getLogger()
    
    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        
    log_level = logging.DEBUG if settings.DEBUG else logging.INFO
    root_logger.setLevel(log_level)
    
    # Create console handler
    console_handler = logging.StreamHandler(sys.stdout)
    
    if settings.is_production:
        # Use JSON formatting in production
        formatter = CustomJsonFormatter(
            "%(timestamp)s %(level)s %(name)s %(message)s"
        )
    else:
        # User-friendly standard logs for local dev
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # Silence verbose libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

# Instantiate logger
logger = logging.getLogger("app")
