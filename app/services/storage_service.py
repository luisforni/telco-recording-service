from __future__ import annotations

import asyncio
import io
import os
from datetime import datetime, timedelta
from functools import partial
from typing import AsyncIterator

import structlog
from minio import Minio
from minio.error import S3Error

logger = structlog.get_logger(__name__)

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
RECORDINGS_BUCKET = os.getenv("RECORDINGS_BUCKET", "call-recordings")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"


class StorageService:
    def __init__(self) -> None:
        self.client = Minio(
            MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            secure=MINIO_SECURE,
        )
        self.bucket = RECORDINGS_BUCKET

    def ensure_bucket(self) -> None:
        try:
            if not self.client.bucket_exists(self.bucket):
                self.client.make_bucket(self.bucket)
                logger.info("bucket_created", bucket=self.bucket)
        except S3Error as exc:
            logger.error("bucket_ensure_error", error=str(exc))
            raise

    def _object_key(self, call_id: str, timestamp: datetime, ext: str) -> str:
        return (
            f"{timestamp.year:04d}/{timestamp.month:02d}/{timestamp.day:02d}"
            f"/{call_id}_{timestamp.strftime('%H%M%S')}.{ext}"
        )

    async def upload(
        self,
        call_id: str,
        data: bytes,
        timestamp: datetime,
        ext: str = "wav",
        content_type: str = "audio/wav",
    ) -> str:
        key = self._object_key(call_id, timestamp, ext)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            partial(
                self.client.put_object,
                self.bucket,
                key,
                io.BytesIO(data),
                length=len(data),
                content_type=content_type,
            ),
        )
        logger.info("recording_uploaded", key=key, bytes=len(data))
        return key

    async def upload_stream(
        self,
        key: str,
        stream: io.IOBase,
        length: int = -1,
        content_type: str = "audio/wav",
    ) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            partial(
                self.client.put_object,
                self.bucket,
                key,
                stream,
                length=length,
                content_type=content_type,
            ),
        )
        logger.info("recording_stream_uploaded", key=key)

    async def download(self, key: str) -> bytes:
        loop = asyncio.get_event_loop()

        def _do_download() -> bytes:
            response = self.client.get_object(self.bucket, key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()

        return await loop.run_in_executor(None, _do_download)

    async def stream_object(self, key: str, chunk_size: int = 65536) -> AsyncIterator[bytes]:
        loop = asyncio.get_event_loop()

        def _open_response():
            return self.client.get_object(self.bucket, key)

        response = await loop.run_in_executor(None, _open_response)
        try:
            while True:
                chunk = await loop.run_in_executor(None, response.read, chunk_size)
                if not chunk:
                    break
                yield chunk
        finally:
            response.close()
            response.release_conn()

    def get_presigned_url(self, key: str, expires_hours: int = 1) -> str:
        url = self.client.presigned_get_object(
            self.bucket,
            key,
            expires=timedelta(hours=expires_hours),
        )
        return url

    def delete(self, key: str) -> None:
        self.client.remove_object(self.bucket, key)
        logger.info("recording_deleted", key=key)

    def object_exists(self, key: str) -> bool:
        try:
            self.client.stat_object(self.bucket, key)
            return True
        except S3Error:
            return False

    def get_object_size(self, key: str) -> int:
        try:
            stat = self.client.stat_object(self.bucket, key)
            return stat.size or 0
        except S3Error:
            return 0
