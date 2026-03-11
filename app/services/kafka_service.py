from __future__ import annotations

import json
import os
from typing import Any

import structlog
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

logger = structlog.get_logger(__name__)

KAFKA_BROKERS = os.getenv("KAFKA_BROKERS", "kafka:9092")
RECORDING_EVENTS_TOPIC = "recording-events"
CALL_EVENTS_TOPIC = "call-events"
ORCHESTRATOR_EVENTS_TOPIC = "orchestrator-events"


class KafkaService:
    def __init__(self) -> None:
        self.producer: AIOKafkaProducer | None = None
        self.consumer: AIOKafkaConsumer | None = None

    async def start_producer(self) -> None:
        self.producer = AIOKafkaProducer(
            bootstrap_servers=KAFKA_BROKERS,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        )
        await self.producer.start()
        logger.info("kafka_producer_started", brokers=KAFKA_BROKERS)

    async def stop_producer(self) -> None:
        if self.producer:
            await self.producer.stop()
            logger.info("kafka_producer_stopped")

    async def start_consumer(self, handler: Any) -> None:
        self.consumer = AIOKafkaConsumer(
            CALL_EVENTS_TOPIC,
            ORCHESTRATOR_EVENTS_TOPIC,
            bootstrap_servers=KAFKA_BROKERS,
            group_id="recording-service",
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            auto_offset_reset="earliest",
        )
        await self.consumer.start()
        logger.info("kafka_consumer_started", topics=[CALL_EVENTS_TOPIC, ORCHESTRATOR_EVENTS_TOPIC])
        try:
            async for msg in self.consumer:
                try:
                    await handler(msg.topic, msg.value)
                except Exception as exc:
                    logger.error("kafka_message_handler_error", error=str(exc))
        finally:
            await self.consumer.stop()

    async def stop_consumer(self) -> None:
        if self.consumer:
            await self.consumer.stop()
            logger.info("kafka_consumer_stopped")

    async def publish_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if not self.producer:
            logger.warning("kafka_producer_not_started")
            return
        message = {"event_type": event_type, **payload}
        await self.producer.send_and_wait(RECORDING_EVENTS_TOPIC, message)
        logger.info("kafka_event_published", event_type=event_type)

    async def publish_recording_started(self, recording_id: str, call_id: str, agent_id: str) -> None:
        await self.publish_event(
            "recording_started",
            {"recording_id": recording_id, "call_id": call_id, "agent_id": agent_id},
        )

    async def publish_recording_stopped(self, recording_id: str, call_id: str) -> None:
        await self.publish_event(
            "recording_stopped",
            {"recording_id": recording_id, "call_id": call_id},
        )

    async def publish_recording_completed(self, recording_id: str, call_id: str, file_path: str) -> None:
        await self.publish_event(
            "recording_completed",
            {"recording_id": recording_id, "call_id": call_id, "file_path": file_path},
        )

    async def publish_recording_failed(self, recording_id: str, call_id: str, reason: str) -> None:
        await self.publish_event(
            "recording_failed",
            {"recording_id": recording_id, "call_id": call_id, "reason": reason},
        )

    async def publish_recording_deleted(self, recording_id: str, call_id: str) -> None:
        await self.publish_event(
            "recording_deleted",
            {"recording_id": recording_id, "call_id": call_id},
        )
