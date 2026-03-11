from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RecordingStatus(str, Enum):
    recording = "recording"
    paused = "paused"
    stopped = "stopped"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class RecordingMetadata(BaseModel):
    tags: list[str] = Field(default_factory=list)
    notes: str = ""
    quality: str = "standard"
    custom: dict[str, Any] = Field(default_factory=dict)


class Recording(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    call_id: str
    agent_id: str = ""
    start_time: datetime = Field(default_factory=datetime.utcnow)
    end_time: datetime | None = None
    duration: float | None = None
    file_path: str = ""
    status: RecordingStatus = RecordingStatus.recording
    format: str = "wav"
    size_bytes: int = 0
    metadata: RecordingMetadata = Field(default_factory=RecordingMetadata)
    transcript: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class RecordingRequest(BaseModel):
    call_id: str
    agent_id: str = ""
    metadata: RecordingMetadata = Field(default_factory=RecordingMetadata)
    format: str = "wav"
    auto_transcribe: bool = True


class RecordingFilter(BaseModel):
    call_id: str | None = None
    agent_id: str | None = None
    status: RecordingStatus | None = None
    start_after: datetime | None = None
    start_before: datetime | None = None
    tags: list[str] = Field(default_factory=list)
    limit: int = 50
    offset: int = 0


class RecordingStats(BaseModel):
    total_recordings: int = 0
    active_recordings: int = 0
    total_duration_seconds: float = 0.0
    total_size_bytes: int = 0
    recordings_by_status: dict[str, int] = Field(default_factory=dict)
    average_duration_seconds: float = 0.0
