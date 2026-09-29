from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import date, timedelta
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(message)s",
)
logger = logging.getLogger("quotation_api")


def log_event(event: str, **fields: object) -> None:
    logger.info(json.dumps({"event": event, **fields}, ensure_ascii=False, default=str))


class QuoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: str = Field(min_length=1, max_length=100)
    destination: str = Field(min_length=1, max_length=100)
    weight_kg: float | None = Field(default=None, gt=0, le=100_000)
    volume_cbm: float | None = Field(default=None, gt=0, le=1_000)
    cargo_type: str | None = Field(default=None, max_length=50)
    transport_mode: Literal["air", "sea"] | None = None
    simulate: (
        Literal[
            "none",
            "timeout",
            "error_500",
            "invalid_json",
            "invalid_business",
            "slow",
        ]
        | None
    ) = None

    @field_validator("origin", "destination", "cargo_type")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def require_weight_or_volume(self) -> QuoteRequest:
        if self.weight_kg is None and self.volume_cbm is None:
            raise ValueError("weight_kg or volume_cbm is required")
        return self


class QuoteResponse(BaseModel):
    quote_id: str
    currency: Literal["USD"]
    base_rate: float
    fuel_surcharge: float
    handling_fee: float
    total_price: float
    valid_until: date
    estimated_transit_days: int
    provider: Literal["Demo Logistics"]


ROUTES = {
    ("shenzhen", "los angeles"): {
        "air_per_kg": 2.05,
        "sea_per_cbm": 120.0,
        "origin_fee": 90.0,
        "air_days": 5,
        "sea_days": 18,
    },
    ("shanghai", "hamburg"): {
        "air_per_kg": 2.25,
        "sea_per_cbm": 135.0,
        "origin_fee": 100.0,
        "air_days": 6,
        "sea_days": 25,
    },
}
DEFAULT_ROUTE = {
    "air_per_kg": 2.30,
    "sea_per_cbm": 145.0,
    "origin_fee": 110.0,
    "air_days": 7,
    "sea_days": 24,
}

LOCATION_ALIASES = {
    "深圳": "shenzhen",
    "szx": "shenzhen",
    "洛杉矶": "los angeles",
    "lax": "los angeles",
}


def normalize_location(value: str) -> str:
    normalized = value.strip().casefold()
    return LOCATION_ALIASES.get(normalized, normalized)


def calculate_quote(payload: QuoteRequest) -> QuoteResponse:
    mode = payload.transport_mode or "air"
    route = ROUTES.get(
        (normalize_location(payload.origin), normalize_location(payload.destination)),
        DEFAULT_ROUTE,
    )

    if mode == "air":
        chargeable_weight = max(payload.weight_kg or 0, (payload.volume_cbm or 0) * 167)
        base_rate = chargeable_weight * route["air_per_kg"] + route["origin_fee"]
        fuel_rate = 0.12
        handling_fee = 85.0
        transit_days = int(route["air_days"])
    else:
        chargeable_volume = max(
            payload.volume_cbm or 0, (payload.weight_kg or 0) / 1_000
        )
        base_rate = chargeable_volume * route["sea_per_cbm"] + route["origin_fee"]
        fuel_rate = 0.08
        handling_fee = 120.0
        transit_days = int(route["sea_days"])

    if (payload.cargo_type or "").lower() == "dangerous":
        handling_fee += 350.0

    base_rate = round(base_rate, 2)
    fuel_surcharge = round(base_rate * fuel_rate, 2)
    subtotal = round(base_rate + fuel_surcharge + handling_fee, 2)
    if subtotal < 1_000:
        handling_fee = round(handling_fee + (1_000 - subtotal), 2)
        subtotal = 1_000.0

    canonical = json.dumps(
        payload.model_dump(exclude={"simulate"}),
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode()).hexdigest()[:6].upper()
    return QuoteResponse(
        quote_id=f"QT-{date.today():%Y%m%d}-{digest}",
        currency="USD",
        base_rate=base_rate,
        fuel_surcharge=fuel_surcharge,
        handling_fee=handling_fee,
        total_price=subtotal,
        valid_until=date.today() + timedelta(days=7),
        estimated_transit_days=transit_days,
        provider="Demo Logistics",
    )


app = FastAPI(title="AI Quotation Demo API", version="0.1.0")


@app.middleware("http")
async def request_trace(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid4().hex
    request.state.request_id = request_id
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    log_event(
        "request_completed",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=round((time.perf_counter() - started) * 1_000, 2),
    )
    return response


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "ai-quotation-demo"}


@app.post("/api/v1/rates/quote", response_model=None)
def create_quote(payload: QuoteRequest, request: Request) -> Response:
    request_id = request.state.request_id
    log_event("quote_requested", request_id=request_id, simulate=payload.simulate)

    if payload.simulate == "timeout":
        time.sleep(3)
    elif payload.simulate == "slow":
        time.sleep(1)
    elif payload.simulate == "error_500":
        raise HTTPException(status_code=500, detail="simulated upstream failure")
    elif payload.simulate == "invalid_json":
        return Response(content='{"quote_id":', media_type="application/json")
    elif payload.simulate == "invalid_business":
        return JSONResponse(
            content={
                "quote_id": "QT-INVALID-BUSINESS",
                "currency": "USD",
                "total_price": 0,
                "valid_until": "2026-10-01",
                "estimated_transit_days": 5,
                "provider": "Demo Logistics",
            }
        )

    quote = calculate_quote(payload)
    log_event("quote_created", request_id=request_id, quote_id=quote.quote_id)
    return JSONResponse(content=quote.model_dump(mode="json"))
