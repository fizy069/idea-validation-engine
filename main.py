"""FastAPI backend for OASIS market simulations."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from oasis_validator.api_models import (
    ErrorResponse,
    GetInterviewsResponse,
    GetResultResponse,
    HealthResponse,
    SLUG_PATTERN,
    SimulateMarketRequest,
    SimulateMarketResponse,
)
from oasis_validator.errors import ApiError
from oasis_validator.rate_limit import InMemoryRateLimiter
from oasis_validator.storage import (
    generate_slug,
    get_validation_by_slug,
    init_storage,
    save_validation,
)

ROOT = Path(__file__).resolve().parent
DEFAULT_PERSONAS = ROOT / "data" / "personas.json"
DEFAULT_RUNS_DIR = ROOT / "data" / "runs"
SLUG_RE = re.compile(SLUG_PATTERN)

logger = logging.getLogger(__name__)

load_dotenv(ROOT / ".env", override=False)


def _parse_allowed_origins() -> list[str]:
    configured = os.environ.get("FRONTEND_ORIGIN", "")
    origins = [item.strip() for item in configured.split(",") if item.strip()]
    if origins:
        return origins
    return ["http://localhost:3000"]


def _get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        candidate = forwarded_for.split(",")[0].strip()
        if candidate:
            return candidate
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _validation_message(exc: RequestValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "Request is invalid."
    first = errors[0]
    location_parts = [
        str(item)
        for item in first.get("loc", ())
        if item not in ("body", "path", "query")
    ]
    location = ".".join(location_parts)
    base_message = str(first.get("msg", "Request is invalid."))
    if location:
        return f"{location} {base_message}"
    return base_message


def _validate_slug_or_raise(slug: str) -> None:
    if not SLUG_RE.fullmatch(slug):
        raise ApiError(
            status_code=400,
            error="invalid_slug",
            message="Slug format is invalid.",
        )


async def _run_market_validation(**kwargs):
    from oasis_validator.pipeline import run_market_validation

    return await run_market_validation(**kwargs)


async def _cleanup_simulation_db(db_path: Path) -> None:
    """Best-effort cleanup for temporary simulation DB files.

    On Windows, sqlite handles can briefly remain open after a run. We retry
    a few times so cleanup never causes a request failure.
    """
    retries = 6
    for attempt in range(retries):
        try:
            if db_path.exists():
                db_path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt == retries - 1:
                logger.warning("Could not delete temp DB due to file lock: %s", db_path)
                return
            await asyncio.sleep(0.2)
        except FileNotFoundError:
            return
        except OSError as exc:
            logger.warning("Could not delete temp DB %s: %s", db_path, exc)
            return


rate_limiter = InMemoryRateLimiter(
    max_requests=int(os.environ.get("SIMULATE_RATE_LIMIT_MAX_REQUESTS", "3")),
    window_seconds=int(os.environ.get("SIMULATE_RATE_LIMIT_WINDOW_SECONDS", "60")),
)

app = FastAPI(title="OASIS Backend", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_parse_allowed_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup() -> None:
    init_storage()
    DEFAULT_RUNS_DIR.mkdir(parents=True, exist_ok=True)


@app.exception_handler(ApiError)
async def _api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(error=exc.error, message=exc.message).model_dump(),
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content=ErrorResponse(
            error="invalid_request",
            message=_validation_message(exc),
        ).model_dump(),
    )


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.post("/simulate/market", response_model=SimulateMarketResponse)
async def simulate_market(
    payload: SimulateMarketRequest,
    request: Request,
) -> SimulateMarketResponse:
    limit_result = await rate_limiter.check(_get_client_ip(request))
    if limit_result.limited:
        raise ApiError(
            status_code=429,
            error="rate_limited",
            message="Try again in a moment.",
            headers={"Retry-After": str(limit_result.retry_after_seconds)},
        )

    simulation_db = DEFAULT_RUNS_DIR / f"simulation_{secrets.token_hex(8)}.db"
    slug = generate_slug()
    try:
        if not DEFAULT_PERSONAS.is_file():
            raise RuntimeError("persona file is missing")

        artifacts = await _run_market_validation(
            idea=payload.idea,
            target_user=payload.targetUser,
            persona_path=DEFAULT_PERSONAS,
            db_path=simulation_db,
            num_vocal=payload.numVocal,
            turns=payload.turns,
            model_name=os.environ.get("MARKET_MODEL", "gpt-4o-mini"),
            judge_model=os.environ.get("JUDGE_MODEL", os.environ.get("MARKET_MODEL", "gpt-4o-mini")),
        )

        result_payload: Dict[str, Any] = {
            "slug": slug,
            "subreddit": payload.subreddit,
            "post": artifacts.post,
            "thread": artifacts.thread,
            "tractionScore": artifacts.traction_score,
            "summary": artifacts.summary,
        }
        validated_result = SimulateMarketResponse.model_validate(result_payload)
        save_validation(
            slug=slug,
            idea=payload.idea,
            target_user=payload.targetUser,
            subreddit=payload.subreddit,
            num_vocal=payload.numVocal,
            turns=payload.turns,
            result=validated_result.model_dump(),
            interviews=artifacts.interviews,
        )
        return validated_result
    except ApiError:
        raise
    except ValueError as exc:
        raise ApiError(
            status_code=400,
            error="invalid_request",
            message=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception("Simulation failed", exc_info=exc)
        raise ApiError(
            status_code=500,
            error="simulation_failed",
            message="The simulation could not complete. Please retry.",
        ) from exc
    finally:
        await _cleanup_simulation_db(simulation_db)


@app.get("/result/{slug}", response_model=GetResultResponse)
async def get_result(slug: str, response: Response) -> GetResultResponse:
    _validate_slug_or_raise(slug)
    persisted = get_validation_by_slug(slug)
    if persisted is None:
        raise ApiError(
            status_code=404,
            error="not_found",
            message="No simulation found for that slug.",
        )

    payload = {
        "slug": persisted.slug,
        "createdAt": persisted.created_at,
        "idea": persisted.idea,
        "targetUser": persisted.target_user,
        "config": {
            "subreddit": persisted.subreddit,
            "numVocal": persisted.num_vocal,
            "turns": persisted.turns,
        },
        "result": persisted.result,
    }
    response.headers["Cache-Control"] = "no-store"
    return GetResultResponse.model_validate(payload)


@app.get("/result/{slug}/interviews", response_model=GetInterviewsResponse)
async def get_interviews(slug: str, response: Response) -> GetInterviewsResponse:
    _validate_slug_or_raise(slug)
    persisted = get_validation_by_slug(slug)
    if persisted is None:
        raise ApiError(
            status_code=404,
            error="not_found",
            message="No simulation found for that slug.",
        )

    payload = {
        "slug": persisted.slug,
        "interviews": persisted.interviews,
    }
    response.headers["Cache-Control"] = "no-store"
    return GetInterviewsResponse.model_validate(payload)
