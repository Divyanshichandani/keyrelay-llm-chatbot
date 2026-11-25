import time
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.schemas import (
    HealthResponse,
    ChatRequest,
    ChatResponse,
    ChatMetadata,
    SessionHistoryResponse,
    ErrorResponse
)
from app.db import (
    init_db,
    check_db_health,
    get_session_system_prompt,
    get_session_history,
    save_chat_turn
)
from app.llm import call_llm, AllProvidersUnavailableException

logger = logging.getLogger("keyrelay.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize database schema
    init_db()
    logger.info("Database initialized successfully at %s", settings.DATABASE_URL)
    yield


app = FastAPI(
    title="LLM Chatbot with Key Failover and Response Caching",
    version="1.0.0",
    lifespan=lifespan
)

# CORS Middleware
origins = settings.cors_origins
if not origins:
    origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


# Exception Handlers
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=jsonable_encoder({"detail": exc.errors()}),
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled server exception: %s", exc, exc_info=False)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "internal error"},
    )


# 1. GET /health
@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    description="Returns 200 once app is running, DB is reachable, and key pool loaded"
)
async def health():
    db_ok = check_db_health()
    if not db_ok:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database health check failed"
        )
    return HealthResponse(
        status="ok",
        keys_configured=len(settings.key_pool),
        cache_enabled=True
    )


# 2. POST /chat
@app.post(
    "/chat",
    response_model=ChatResponse,
    summary="Chat completion",
    description="Processes chat completion with multi-turn history, caching, and key failover"
)
async def chat(
    request: ChatRequest,
    x_simulate_failure: Optional[str] = Header(None, alias="X-Simulate-Failure")
):
    req_start = time.perf_counter()

    # Determine system prompt for this session
    persisted_prompt = get_session_system_prompt(request.session_id)
    if request.system_prompt is not None and request.system_prompt.strip():
        active_system_prompt = request.system_prompt.strip()
    elif persisted_prompt is not None:
        active_system_prompt = persisted_prompt
    else:
        active_system_prompt = settings.DEFAULT_SYSTEM_PROMPT

    # Retrieve prior conversation messages
    prior_messages = get_session_history(request.session_id) or []

    # Build messages list for LLM gateway
    llm_messages = [{"role": "system", "content": active_system_prompt}]
    for m in prior_messages:
        llm_messages.append({"role": m["role"], "content": m["content"]})
    llm_messages.append({"role": "user", "content": request.message})

    # Call LLM gateway with failover & caching
    try:
        llm_result = call_llm(
            messages=llm_messages,
            simulation_header=x_simulate_failure,
            model=settings.LLM_MODEL,
            timeout=settings.LLM_TIMEOUT
        )
    except AllProvidersUnavailableException:
        # A request that fails must NOT be added to session history
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="all providers unavailable"
        )
    except Exception as exc:
        logger.error("Unexpected error in LLM execution: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="internal error"
        )

    # Server-measured latency around the whole request handling
    total_latency_ms = int((time.perf_counter() - req_start) * 1000)

    # Build final metadata
    metadata_payload = {
        "resolution_source": llm_result["metadata"]["resolution_source"],
        "model": llm_result["metadata"]["model"],
        "latency_ms": total_latency_ms,
        "input_tokens": llm_result["metadata"]["input_tokens"],
        "output_tokens": llm_result["metadata"]["output_tokens"],
        "key_id": llm_result["metadata"]["key_id"],
        "fallback_attempts": llm_result["metadata"]["fallback_attempts"]
    }

    # Only persist turn to SQLite after LLM call succeeded
    save_chat_turn(
        session_id=request.session_id,
        user_message=request.message,
        assistant_reply=llm_result["reply"],
        assistant_metadata=metadata_payload,
        system_prompt=active_system_prompt if (request.system_prompt or not persisted_prompt) else None
    )

    return ChatResponse(
        session_id=request.session_id,
        reply=llm_result["reply"],
        metadata=ChatMetadata(**metadata_payload)
    )


# 3. GET /sessions/{session_id}/messages
@app.get(
    "/sessions/{session_id}/messages",
    response_model=SessionHistoryResponse,
    summary="Get session history",
    description="Returns stored history of one session from SQLite without system prompt"
)
async def get_messages(session_id: str):
    if not (1 <= len(session_id.strip()) <= 128):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid session_id"
        )

    history = get_session_history(session_id.strip())
    if history is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="session not found"
        )

    return SessionHistoryResponse(
        session_id=session_id.strip(),
        messages=history
    )
