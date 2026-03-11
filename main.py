from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Gauge, Histogram, make_asgi_app

from app.api.routes import router
from app.services.kafka_service import KafkaService
from app.services.recording_service import RecordingService
from app.services.storage_service import StorageService

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ]
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------
ACTIVE_RECORDINGS = Gauge("recording_active_total", "Number of active recordings")
RECORDINGS_TOTAL = Counter("recording_requests_total", "Total recording requests", ["status"])
RECORDING_DURATION = Histogram(
    "recording_duration_seconds",
    "Recording duration in seconds",
    buckets=[10, 30, 60, 120, 300, 600, 1800, 3600],
)
STORAGE_USAGE = Gauge("recording_storage_bytes", "Total storage used by recordings")
DOWNLOAD_REQUESTS = Counter("recording_download_requests_total", "Total download/stream requests")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    storage = StorageService()
    try:
        storage.ensure_bucket()
    except Exception as exc:
        logger.warning("storage_init_warning", error=str(exc))

    kafka = KafkaService()
    recording_service = RecordingService(storage=storage, kafka=kafka)

    app.state.recording_service = recording_service
    app.state.kafka = kafka
    app.state.storage = storage

    try:
        await kafka.start_producer()
    except Exception as exc:
        logger.warning("kafka_producer_init_warning", error=str(exc))

    consumer_task: asyncio.Task | None = None
    try:
        consumer_task = asyncio.create_task(
            kafka.start_consumer(recording_service.handle_kafka_event)
        )
    except Exception as exc:
        logger.warning("kafka_consumer_init_warning", error=str(exc))

    logger.info("telco_recording_service_started")
    yield

    if consumer_task:
        consumer_task.cancel()
        try:
            await consumer_task
        except asyncio.CancelledError:
            pass

    try:
        await kafka.stop_producer()
    except Exception as exc:
        logger.warning("kafka_producer_stop_warning", error=str(exc))

    logger.info("telco_recording_service_stopped")


app = FastAPI(
    title="Telco Recording Service",
    description="Microservice for call recording, storage, retrieval, and playback",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

app.include_router(router)


@app.get("/healthz", tags=["health"])
async def healthz() -> dict:
    return {"status": "ok", "service": "telco-recording-service"}


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8009"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
