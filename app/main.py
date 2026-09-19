"""FastAPI application exposing recurring-rule expansion."""

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .errors import DomainError
from .models import OccurrenceRequest, OccurrenceResponse, Window
from .recurrence import expand, format_utc

app = FastAPI(
    title="Recurring Time Rule Expansion API",
    version="1.0.0",
    description=(
        "Expands RFC 5545 RRULEs into concrete occurrences within a UTC query "
        "window. Local times are preserved together with their UTC offset; "
        "non-existent local times (DST gap) are skipped and repeated local "
        "times (DST fold) are returned once per fold."
    ),
)


@app.exception_handler(DomainError)
async def domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "Request payload failed schema validation",
                "details": {"errors": jsonable_encoder(exc.errors())},
            }
        },
    )


@app.get("/", tags=["meta"])
def root() -> dict:
    """Service information."""
    return {
        "service": app.title,
        "version": app.version,
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health", tags=["meta"])
def health() -> dict:
    """Liveness/readiness probe."""
    return {"status": "ok"}


@app.post(
    "/occurrences",
    response_model=OccurrenceResponse,
    tags=["occurrences"],
    summary="Expand a recurrence rule into occurrences within a window",
)
def post_occurrences(req: OccurrenceRequest) -> OccurrenceResponse:
    occurrences, skipped_nonexistent, window_start, window_end = expand(req)
    return OccurrenceResponse(
        timezone=req.timezone,
        window=Window(start=format_utc(window_start), end=format_utc(window_end)),
        count=len(occurrences),
        skipped_nonexistent=skipped_nonexistent,
        occurrences=occurrences,
    )
