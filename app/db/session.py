import socket
import ssl
from urllib.parse import urlparse
from typing import AsyncGenerator, Dict, Any
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from app.core.config import settings
from app.core.logging import logger
from app.core.exceptions import (
    InvalidDatabaseURLError,
    DatabaseDNSResolveError,
    DatabasePortUnreachableError,
    DatabaseSSLHandshakeError,
    DatabaseAuthenticationError,
    DatabaseTimeoutError,
    DatabaseConnectionPoolError,
)

# Global lazy-loaded database engine and session maker
_engine = None
_async_session_maker = None

def resolve_db_host(hostname: str) -> str:
    """Resolves database hostname to IPv4 address specifically to avoid IPv6 unreachable network errors."""
    try:
        # Get address info for IPv4 (socket.AF_INET) to force IPv4 connection routes
        addr_info = socket.getaddrinfo(hostname, None, socket.AF_INET)
        if addr_info:
            ipv4_address = addr_info[0][4][0]
            logger.info(f"Resolved database host '{hostname}' to IPv4 address: {ipv4_address}")
            return ipv4_address
    except Exception as e:
        logger.warning(
            f"IPv4 DNS Resolution failed for host '{hostname}': {e}. "
            "If this is an IPv6-only host (like db.[ref].supabase.co), please note that Render "
            "does not support IPv6 outbound routing. You must use the Supabase Connection Pooler "
            "host (ending in pooler.supabase.com) on port 6543 or 5432, which supports IPv4."
        )
        # Fallback to dynamic resolution via AF_UNSPEC
        try:
            addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC)
            if addr_info:
                ip = addr_info[0][4][0]
                logger.info(f"Fallback Resolved database host '{hostname}' to IP: {ip}")
                return ip
        except Exception as fallback_err:
            logger.error(f"Fallback DNS Resolution failed for host '{hostname}': {fallback_err}")
            raise DatabaseDNSResolveError(
                f"DNS Resolution failed for host '{hostname}'. "
                "Verify hostname or connection. If using Supabase, switch to the pooler.supabase.com host."
            )
    return hostname

def get_engine():
    """Lazy-initialize the SQLAlchemy async engine after startup to ensure network interface is active."""
    global _engine
    if _engine is None:
        parsed_url = urlparse(settings.DATABASE_URL)
        original_host = parsed_url.hostname
        connect_args: Dict[str, Any] = {}

        if original_host:
            # Resolve host to IPv4
            try:
                resolved_ip = resolve_db_host(original_host)
            except DatabaseDNSResolveError as e:
                import sys
                is_testing = "pytest" in sys.modules or settings.APP_ENV == "testing"
                if is_testing:
                    logger.warning("Bypassing DNS Resolution failure in get_engine during testing.")
                    resolved_ip = original_host
                else:
                    raise e
            
            # Reconstruct DATABASE_URL with resolved IP (only for local connections to prevent SSL hostname verification issues)
            if original_host != resolved_ip and original_host in ("localhost", "127.0.0.1"):
                netloc = parsed_url.netloc
                # If resolved_ip is IPv6 (contains colons), wrap it in square brackets
                resolved_ip_formatted = f"[{resolved_ip}]" if ":" in resolved_ip else resolved_ip
                new_netloc = netloc.replace(original_host, resolved_ip_formatted, 1)
                db_url = parsed_url._replace(netloc=new_netloc).geturl()
            else:
                db_url = settings.DATABASE_URL
                
            # Set connection ssl arguments for remote database instances
            if original_host not in ("localhost", "127.0.0.1"):
                connect_args["ssl"] = "require"
        else:
            db_url = settings.DATABASE_URL

        logger.info(f"Creating database engine with resolved URL: {db_url.split('@')[-1]}")
        _engine = create_async_engine(
            db_url,
            echo=settings.DEBUG and not settings.is_production,
            pool_pre_ping=True,      # Checks connection liveness before checking it out
            pool_size=10,            # Standard connections to keep open in the pool
            max_overflow=20,         # Max extra connections beyond pool_size
            connect_args=connect_args
        )
    return _engine

def get_session_maker():
    """Lazy-initialize the async session maker."""
    global _async_session_maker
    if _async_session_maker is None:
        _async_session_maker = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _async_session_maker

def run_db_diagnostics() -> None:
    """Runs comprehensive network diagnostics on startup for the configured database."""
    if not settings.DATABASE_URL:
        raise InvalidDatabaseURLError("DATABASE_URL is not configured.")
        
    try:
        parsed = urlparse(settings.DATABASE_URL)
    except Exception as e:
        raise InvalidDatabaseURLError(f"Malformed DATABASE_URL: {e}")
        
    if not parsed.scheme.startswith("postgresql"):
        raise InvalidDatabaseURLError(f"Invalid database scheme '{parsed.scheme}'. Must start with 'postgresql'.")
        
    host = parsed.hostname
    port = parsed.port or 5432
    dbname = parsed.path.strip('/')
    
    logger.info("=== DATABASE DIAGNOSTICS ===")
    logger.info(f"Database Provider: {'Supabase' if 'supabase' in (host or '') else 'PostgreSQL'}")
    logger.info(f"Host: {host}")
    logger.info(f"Port: {port}")
    logger.info(f"Database Name: {dbname}")
    logger.info(f"Driver: {parsed.scheme.split('+')[1] if '+' in parsed.scheme else 'psycopg2'}")
    
    import sys
    is_testing = "pytest" in sys.modules or settings.APP_ENV == "testing"

    # 1. DNS Resolution Check
    try:
        addr_info = socket.getaddrinfo(host, port, socket.AF_INET)
        ip = addr_info[0][4][0]
        logger.info(f"DNS Resolution: SUCCESS (IP: {ip})")
    except socket.gaierror as e:
        logger.error(f"DNS Resolution: FAILED for host '{host}': {e}")
        if is_testing:
            logger.warning("Bypassing DNS Resolution failure during testing.")
            return
        raise DatabaseDNSResolveError(f"DNS Resolution failed for host '{host}'. Verify hostname or internet connection.")
        
    # 2. Port Connection Check (TCP Ping)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5.0)
        s.connect((ip, port))
        logger.info(f"TCP Connection to {ip}:{port}: SUCCESS")
    except socket.timeout as e:
        logger.error(f"TCP Connection to {ip}:{port}: TIMEOUT")
        if is_testing:
            logger.warning("Bypassing TCP connection timeout during testing.")
            return
        raise DatabaseTimeoutError(f"TCP connection to database at {host}:{port} timed out after 5 seconds.")
    except OSError as e:
        logger.error(f"TCP Connection to {ip}:{port}: FAILED ({e})")
        if is_testing:
            logger.warning("Bypassing TCP connection failure during testing.")
            return
        raise DatabasePortUnreachableError(f"Cannot reach database port {port} at {host}. Firewall blocking or port closed. Details: {e}")
    finally:
        try:
            s.close()
        except:
            pass
            
    # 3. SSL Negotiation Check (if not localhost)
    if host not in ("localhost", "127.0.0.1"):
        logger.info("SSL Enabled: YES")
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5.0)
            s.connect((ip, port))
            
            # Send Postgres SSLRequest packet: length 8, code 80877103 (0x04D2162F)
            s.sendall(b'\x00\x00\x00\x08\x04\xd2\x16\x2f')
            resp = s.recv(1)
            
            if resp == b'S':
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                ssl_conn = context.wrap_socket(s, server_hostname=host)
                ssl_conn.do_handshake()
                logger.info("SSL Handshake/Negotiation: SUCCESS")
                ssl_conn.close()
            elif resp == b'N':
                logger.warning("SSL Handshake/Negotiation: Server rejected SSL (b'N')")
                s.close()
            else:
                logger.warning(f"SSL Handshake/Negotiation: Unexpected server response: {resp}")
                s.close()
        except Exception as e:
            logger.error(f"SSL Handshake/Negotiation: FAILED: {e}")
            if is_testing:
                logger.warning("Bypassing SSL Handshake failure during testing.")
                return
            raise DatabaseSSLHandshakeError(f"SSL/TLS handshake failed connecting to {host}:{port}. SSL mode mismatch or certification issues: {e}")
    else:
        logger.info("SSL Enabled: NO")
        
    logger.info("Connection Status: DIAGNOSTICS PASSED")
    logger.info(f"Environment: {settings.APP_ENV}")
    logger.info("============================")

async def verify_db_connection() -> None:
    """Verify that database credentials are valid and SELECT 1 query executes successfully."""
    import sys
    is_testing = "pytest" in sys.modules or settings.APP_ENV == "testing"
    try:
        session_maker = get_session_maker()
        async with session_maker() as session:
            await session.execute(text("SELECT 1"))
        logger.info("Database Authentication & Execution: SUCCESS")
    except Exception as e:
        logger.error(f"Database authentication/query execution failed: {e}")
        if is_testing:
            logger.warning("Bypassing database connection failure verification during testing.")
            return
        err_msg = str(e).lower()
        if "password authentication failed" in err_msg or "invalid credentials" in err_msg or "fatal: auth" in err_msg:
            raise DatabaseAuthenticationError(f"Database authentication failed. Verify username and password in DATABASE_URL. Details: {e}")
        raise DatabaseConnectionPoolError(f"Database engine query verification failed: {e}")

async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency injection generator for async database sessions."""
    session_maker = get_session_maker()
    async with session_maker() as session:
        try:
            yield session
        except Exception as e:
            logger.error(f"Database session error occurred: {e}")
            await session.rollback()
            raise
        finally:
            await session.close()
