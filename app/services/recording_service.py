from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta
from typing import Any

import httpx
import structlog

from app.models.recording import (
    Recording,
    RecordingFilter,
    RecordingMetadata,
    RecordingRequest,
    RecordingStats,
    RecordingStatus,
)
from app.services.kafka_service import KafkaService
from app.services.storage_service import StorageService

logger = structlog.get_logger(__name__)

SIP_GATEWAY_URL = os.getenv("SIP_GATEWAY_URL", "http://sip-gateway:8002")
ASR_SERVICE_URL = os.getenv("ASR_SERVICE_URL", "http://asr-service:8004")
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "90"))
AUTO_TRANSCRIBE = os.getenv("AUTO_TRANSCRIBE", "true").lower() == "true"


class RecordingService:
    def __init__(self, storage: StorageService, kafka: KafkaService) -> None:
        self.storage = storage
        self.kafka = kafka
        self._recordings: dict[str, Recording] = {}
        self._lock = asyncio.Lock()
        self._background_tasks: set[asyncio.Task] = set()

    async def start_recording(self, request: RecordingRequest) -> Recording:
        recording = Recording(
            call_id=request.call_id,
            agent_id=request.agent_id,
            metadata=request.metadata,
            format=request.format,
            status=RecordingStatus.recording,
        )

        async with self._lock:
            self._recordings[recording.id] = recording

        logger.info("recording_started", recording_id=recording.id, call_id=request.call_id)

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"{SIP_GATEWAY_URL}/recording/start",
                    json={"call_id": request.call_id, "recording_id": recording.id},
                )
        except Exception as exc:
            logger.warning("sip_gateway_notify_error", error=str(exc))

        await self.kafka.publish_recording_started(recording.id, recording.call_id, recording.agent_id)
        return recording

    async def stop_recording(self, recording_id: str) -> Recording:
        recording = await self._get_or_raise(recording_id)
        recording.status = RecordingStatus.stopped
        recording.end_time = datetime.utcnow()
        if recording.start_time:
            recording.duration = (recording.end_time - recording.start_time).total_seconds()
        recording.updated_at = datetime.utcnow()

        await self.kafka.publish_recording_stopped(recording.id, recording.call_id)
        logger.info("recording_stopped", recording_id=recording_id)
        return recording

    async def pause_recording(self, recording_id: str) -> Recording:
        recording = await self._get_or_raise(recording_id)
        if recording.status != RecordingStatus.recording:
            raise ValueError(f"Recording {recording_id} is not active")
        recording.status = RecordingStatus.paused
        recording.updated_at = datetime.utcnow()
        logger.info("recording_paused", recording_id=recording_id)
        return recording

    async def resume_recording(self, recording_id: str) -> Recording:
        recording = await self._get_or_raise(recording_id)
        if recording.status != RecordingStatus.paused:
            raise ValueError(f"Recording {recording_id} is not paused")
        recording.status = RecordingStatus.recording
        recording.updated_at = datetime.utcnow()
        logger.info("recording_resumed", recording_id=recording_id)
        return recording

    async def get_recording(self, recording_id: str) -> Recording:
        return await self._get_or_raise(recording_id)

    async def delete_recording(self, recording_id: str) -> None:
        recording = await self._get_or_raise(recording_id)
        if recording.file_path:
            try:
                self.storage.delete(recording.file_path)
            except Exception as exc:
                logger.warning("storage_delete_error", error=str(exc))

        async with self._lock:
            self._recordings.pop(recording_id, None)

        await self.kafka.publish_recording_deleted(recording_id, recording.call_id)
        logger.info("recording_deleted", recording_id=recording_id)

    async def upload_audio(self, recording_id: str, data: bytes, ext: str = "wav") -> Recording:
        recording = await self._get_or_raise(recording_id)
        recording.status = RecordingStatus.processing
        recording.updated_at = datetime.utcnow()

        content_type = f"audio/{ext}"
        key = await self.storage.upload(recording.call_id, data, recording.start_time, ext, content_type)
        recording.file_path = key
        recording.size_bytes = len(data)
        recording.status = RecordingStatus.completed
        recording.updated_at = datetime.utcnow()

        await self.kafka.publish_recording_completed(recording.id, recording.call_id, key)

        if AUTO_TRANSCRIBE:
            task = asyncio.create_task(self._trigger_transcription(recording))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

        logger.info("recording_audio_uploaded", recording_id=recording_id, key=key)
        return recording

    async def _trigger_transcription(self, recording: Recording) -> None:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                await client.post(
                    f"{ASR_SERVICE_URL}/transcribe",
                    json={"recording_id": recording.id, "file_path": recording.file_path},
                )
            logger.info("transcription_triggered", recording_id=recording.id)
        except Exception as exc:
            logger.warning("transcription_trigger_error", recording_id=recording.id, error=str(exc))

    async def update_metadata(self, recording_id: str, metadata: RecordingMetadata) -> Recording:
        recording = await self._get_or_raise(recording_id)
        recording.metadata = metadata
        recording.updated_at = datetime.utcnow()
        return recording

    async def add_tag(self, recording_id: str, tag: str) -> Recording:
        recording = await self._get_or_raise(recording_id)
        if tag not in recording.metadata.tags:
            recording.metadata.tags.append(tag)
        recording.updated_at = datetime.utcnow()
        return recording

    async def set_transcript(self, recording_id: str, transcript: str) -> Recording:
        recording = await self._get_or_raise(recording_id)
        recording.transcript = transcript
        recording.updated_at = datetime.utcnow()
        return recording

    async def list_recordings(self, filters: RecordingFilter | None = None) -> list[Recording]:
        async with self._lock:
            recordings = list(self._recordings.values())

        if not filters:
            return recordings

        if filters.call_id:
            recordings = [r for r in recordings if r.call_id == filters.call_id]
        if filters.agent_id:
            recordings = [r for r in recordings if r.agent_id == filters.agent_id]
        if filters.status:
            recordings = [r for r in recordings if r.status == filters.status]
        if filters.start_after:
            recordings = [r for r in recordings if r.start_time >= filters.start_after]
        if filters.start_before:
            recordings = [r for r in recordings if r.start_time <= filters.start_before]
        if filters.tags:
            recordings = [r for r in recordings if all(t in r.metadata.tags for t in filters.tags)]

        return recordings[filters.offset : filters.offset + filters.limit]

    async def search_recordings(self, query: str) -> list[Recording]:
        async with self._lock:
            recordings = list(self._recordings.values())
        q = query.lower()
        return [
            r for r in recordings
            if q in r.call_id.lower()
            or q in r.agent_id.lower()
            or q in (r.metadata.notes or "").lower()
            or any(q in tag.lower() for tag in r.metadata.tags)
        ]

    async def get_recordings_by_call(self, call_id: str) -> list[Recording]:
        return await self.list_recordings(RecordingFilter(call_id=call_id, limit=1000))

    async def get_recordings_by_agent(self, agent_id: str) -> list[Recording]:
        return await self.list_recordings(RecordingFilter(agent_id=agent_id, limit=1000))

    async def get_recordings_by_date_range(
        self, start: datetime, end: datetime
    ) -> list[Recording]:
        return await self.list_recordings(
            RecordingFilter(start_after=start, start_before=end, limit=1000)
        )

    async def get_stats(self) -> RecordingStats:
        async with self._lock:
            recordings = list(self._recordings.values())

        status_counts: dict[str, int] = {}
        total_duration = 0.0
        total_size = 0

        for r in recordings:
            status_counts[r.status.value] = status_counts.get(r.status.value, 0) + 1
            if r.duration:
                total_duration += r.duration
            total_size += r.size_bytes

        total = len(recordings)
        active = status_counts.get(RecordingStatus.recording.value, 0)
        avg_duration = total_duration / total if total > 0 else 0.0

        return RecordingStats(
            total_recordings=total,
            active_recordings=active,
            total_duration_seconds=total_duration,
            total_size_bytes=total_size,
            recordings_by_status=status_counts,
            average_duration_seconds=avg_duration,
        )

    async def enforce_retention(self) -> int:
        cutoff = datetime.utcnow() - timedelta(days=RETENTION_DAYS)
        async with self._lock:
            to_delete = [
                r for r in self._recordings.values()
                if r.status == RecordingStatus.completed and r.start_time < cutoff
            ]

        deleted = 0
        for recording in to_delete:
            try:
                await self.delete_recording(recording.id)
                deleted += 1
            except Exception as exc:
                logger.error("retention_delete_error", recording_id=recording.id, error=str(exc))

        if deleted:
            logger.info("retention_enforced", deleted=deleted)
        return deleted

    async def handle_kafka_event(self, topic: str, event: dict[str, Any]) -> None:
        event_type = event.get("event_type", "")
        logger.info("kafka_event_received", topic=topic, event_type=event_type)

        if topic == "call-events" and event_type == "call_started":
            call_id = event.get("call_id", "")
            agent_id = event.get("agent_id", "")
            if call_id:
                await self.start_recording(
                    RecordingRequest(call_id=call_id, agent_id=agent_id)
                )
        elif topic == "call-events" and event_type == "call_ended":
            call_id = event.get("call_id", "")
            recordings = await self.get_recordings_by_call(call_id)
            for rec in recordings:
                if rec.status == RecordingStatus.recording:
                    await self.stop_recording(rec.id)

    async def _get_or_raise(self, recording_id: str) -> Recording:
        async with self._lock:
            recording = self._recordings.get(recording_id)
        if not recording:
            raise KeyError(f"Recording {recording_id} not found")
        return recording
