from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import router as api_v1_router
from app.core.config import settings
from app.core.exceptions import AppException
from app.core.logging import logger, setup_logging
from app.core.middleware import RequestLoggingMiddleware
from app.db.chroma import chroma_manager

# Configure logging at startup
setup_logging()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup hook: Initialize external clients
    logger.info("Running database connectivity diagnostics...")
    try:
        from app.db.session import run_db_diagnostics, verify_db_connection
        run_db_diagnostics()
        await verify_db_connection()
    except Exception as e:
        logger.critical(f"Database configuration validation failed! Web server will continue booting. Error: {e}")

    logger.info("Initializing external service connection pools...")
    chroma_manager.connect()
    
    try:
        from app.services.rag_service import RAGService
        rag = RAGService()
        await rag.initialize_collection()
    except Exception as e:
        logger.error(f"Failed to auto-initialize RAG collection: {e}")
        
    # Pre-load heavy Speech AI models in the background to prevent startup timeouts and memory spikes
    try:
        import asyncio
        from app.services.speech.tts.melotts_provider import MeloTTSProvider
        from app.services.speech.stt.faster_whisper_provider import FasterWhisperProvider
        import os

        async def load_models_sequentially():
            # Wait 5 seconds after startup to ensure web server is fully responsive and listening
            await asyncio.sleep(5.0)
            
            try:
                logger.info("Pre-loading MeloTTS model in the background...")
                await MeloTTSProvider._get_model_and_speaker()
                logger.info("MeloTTS model pre-loaded successfully.")
            except Exception as e:
                logger.error(f"Failed to pre-load MeloTTS: {e}")
                
            try:
                logger.info("Pre-loading CTranslate2 Whisper model in the background...")
                model_size = os.environ.get("WHISPER_MODEL", "large-v3-turbo")
                await FasterWhisperProvider._get_model(model_size)
                logger.info("CTranslate2 Whisper model pre-loaded successfully.")
            except Exception as e:
                logger.error(f"Failed to pre-load CTranslate2 Whisper: {e}")

        asyncio.create_task(load_models_sequentially())
    except Exception as e:
        logger.error(f"Failed to register background Speech AI models preloading: {e}")
    
    yield
    
    # Shutdown hook: Clean up pools
    logger.info("Shutting down external service connection pools...")
    # Clean up engine connection pool
    from app.db.session import get_engine
    await get_engine().dispose()
    logger.info("Database connection pool disposed.")

app = FastAPI(
    title=settings.APP_NAME,
    description="Backend service powering outbound cold calls and inbound support bots.",
    version="0.1.0",
    debug=settings.DEBUG,
    lifespan=lifespan,
)

# Apply CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Apply request tracing middleware
app.add_middleware(RequestLoggingMiddleware)

# Custom exception handler for uniform responses
@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    logger.error(f"Application error occurred: {exc.message} (status: {exc.status_code})")
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message, "status": "error"}
    )

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled system error occurred: {str(exc)}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An internal system error occurred.", "status": "error"}
    )

# Include Router
app.include_router(api_v1_router, prefix="/api/v1")

# Serve Voice Agent Demo Page
@app.get("/voice-agent")
async def voice_agent_page():
    from fastapi.responses import FileResponse
    return FileResponse("static/voice-agent.html")

# Mount Static Files Dashboard
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root_redirect():
    return RedirectResponse(url="/static/index.html")
