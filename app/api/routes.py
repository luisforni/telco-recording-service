from __future__ import annotations

from datetime import datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.models.recording import (
    Recording,
    RecordingFilter,
    RecordingMetadata,
    RecordingRequest,
    RecordingStats,
)
from app.services.recording_service import RecordingService

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/recordings", tags=["recordings"])


def get_service(request: Request) -> RecordingService:
    return request.app.state.recording_service


ServiceDep = Annotated[RecordingService, Depends(get_service)]


# ---------------------------------------------------------------------------
# Recording Control
# ---------------------------------------------------------------------------


@router.post("/start", response_model=Recording, status_code=status.HTTP_201_CREATED)
async def start_recording(body: RecordingRequest, svc: ServiceDep) -> Recording:
    try:
        return await svc.start_recording(body)
    except Exception as exc:
        logger.error("start_recording_error", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{recording_id}/stop", response_model=Recording)
async def stop_recording(recording_id: str, svc: ServiceDep) -> Recording:
    try:
        return await svc.stop_recording(recording_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{recording_id}/pause", response_model=Recording)
async def pause_recording(recording_id: str, svc: ServiceDep) -> Recording:
    try:
        return await svc.pause_recording(recording_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/{recording_id}/resume", response_model=Recording)
async def resume_recording(recording_id: str, svc: ServiceDep) -> Recording:
    try:
        return await svc.resume_recording(recording_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/stats", response_model=RecordingStats)
async def get_stats(svc: ServiceDep) -> RecordingStats:
    return await svc.get_stats()


@router.get("/search", response_model=list[Recording])
async def search_recordings(
    q: Annotated[str, Query(description="Search query")] = "",
    svc: ServiceDep = ...,
) -> list[Recording]:
    return await svc.search_recordings(q)


@router.get("/call/{call_id}", response_model=list[Recording])
async def get_by_call(call_id: str, svc: ServiceDep) -> list[Recording]:
    return await svc.get_recordings_by_call(call_id)


@router.get("/agent/{agent_id}", response_model=list[Recording])
async def get_by_agent(agent_id: str, svc: ServiceDep) -> list[Recording]:
    return await svc.get_recordings_by_agent(agent_id)


@router.get("/date-range", response_model=list[Recording])
async def get_by_date_range(
    start: datetime,
    end: datetime,
    svc: ServiceDep = ...,
) -> list[Recording]:
    return await svc.get_recordings_by_date_range(start, end)


@router.get("", response_model=list[Recording])
async def list_recordings(
    call_id: str | None = None,
    agent_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    svc: ServiceDep = ...,
) -> list[Recording]:
    filters = RecordingFilter(call_id=call_id, agent_id=agent_id, limit=limit, offset=offset)
    return await svc.list_recordings(filters)


@router.get("/{recording_id}", response_model=Recording)
async def get_recording(recording_id: str, svc: ServiceDep) -> Recording:
    try:
        return await svc.get_recording(recording_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.delete("/{recording_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_recording(recording_id: str, svc: ServiceDep) -> None:
    try:
        await svc.delete_recording(recording_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ---------------------------------------------------------------------------
# Recording Retrieval
# ---------------------------------------------------------------------------


@router.get("/{recording_id}/download")
async def download_recording(recording_id: str, svc: ServiceDep) -> StreamingResponse:
    try:
        recording = await svc.get_recording(recording_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if not recording.file_path:
        raise HTTPException(status_code=404, detail="No file available for this recording")

    try:
        data = await svc.storage.download(recording.file_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Download failed: {exc}")

    filename = f"{recording.call_id}_{recording.id}.{recording.format}"
    return StreamingResponse(
        iter([data]),
        media_type=f"audio/{recording.format}",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{recording_id}/stream")
async def stream_recording(recording_id: str, svc: ServiceDep) -> StreamingResponse:
    try:
        recording = await svc.get_recording(recording_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if not recording.file_path:
        raise HTTPException(status_code=404, detail="No file available for this recording")

    return StreamingResponse(
        svc.storage.stream_object(recording.file_path),
        media_type=f"audio/{recording.format}",
    )


@router.get("/{recording_id}/url")
async def get_signed_url(
    recording_id: str,
    expires_hours: int = Query(default=1, ge=1, le=24),
    svc: ServiceDep = ...,
) -> dict:
    try:
        recording = await svc.get_recording(recording_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if not recording.file_path:
        raise HTTPException(status_code=404, detail="No file available for this recording")

    try:
        url = svc.storage.get_presigned_url(recording.file_path, expires_hours=expires_hours)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"URL generation failed: {exc}")

    return {"url": url, "expires_hours": expires_hours}


# ---------------------------------------------------------------------------
# Metadata & Analytics
# ---------------------------------------------------------------------------


@router.put("/{recording_id}/metadata", response_model=Recording)
async def update_metadata(recording_id: str, metadata: RecordingMetadata, svc: ServiceDep) -> Recording:
    try:
        return await svc.update_metadata(recording_id, metadata)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/{recording_id}/transcript")
async def get_transcript(recording_id: str, svc: ServiceDep) -> dict:
    try:
        recording = await svc.get_recording(recording_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if recording.transcript is None:
        raise HTTPException(status_code=404, detail="Transcript not available")

    return {"recording_id": recording_id, "transcript": recording.transcript}


@router.post("/{recording_id}/tag", response_model=Recording)
async def add_tag(recording_id: str, body: dict, svc: ServiceDep) -> Recording:
    tag = body.get("tag", "")
    if not tag:
        raise HTTPException(status_code=400, detail="tag is required")
    try:
        return await svc.add_tag(recording_id, tag)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
