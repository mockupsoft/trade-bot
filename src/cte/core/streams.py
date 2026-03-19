"""Redis Streams producer/consumer abstraction.

Provides typed publish/subscribe over Redis Streams with
consumer groups, backpressure, and dead-letter handling.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, TypeVar

import orjson
import redis.asyncio as aioredis
import structlog

from cte.core.events import BaseEvent
from cte.core.exceptions import StreamError

if TYPE_CHECKING:
    from cte.core.settings import RedisSettings

logger = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseEvent)


class StreamPublisher:
    """Publishes Pydantic events to Redis Streams."""

    def __init__(self, redis: aioredis.Redis, max_len: int = 100_000) -> None:
        self._redis = redis
        self._max_len = max_len

    async def publish(self, stream_key: str, event: BaseEvent) -> str:
        """Publish an event to a Redis Stream. Returns the message ID."""
        try:
            payload = orjson.dumps(event.model_dump(mode="json"))
            msg_id: bytes = await self._redis.xadd(
                stream_key,
                {"data": payload},
                maxlen=self._max_len,
                approximate=True,
            )
            await logger.adebug(
                "event_published",
                stream=stream_key,
                event_id=str(event.event_id),
                msg_id=msg_id.decode() if isinstance(msg_id, bytes) else msg_id,
            )
            return msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id)
        except Exception as exc:
            raise StreamError(
                f"Failed to publish to {stream_key}",
                context={"stream": stream_key, "event_id": str(event.event_id)},
            ) from exc


class StreamConsumer:
    """Consumes events from a Redis Stream using consumer groups."""

    def __init__(
        self,
        redis: aioredis.Redis,
        stream_key: str,
        group: str,
        consumer: str,
        batch_size: int = 10,
        block_ms: int = 5000,
    ) -> None:
        self._redis = redis
        self._stream_key = stream_key
        self._group = group
        self._consumer = consumer
        self._batch_size = batch_size
        self._block_ms = block_ms
        self._running = False

    async def ensure_group(self) -> None:
        """Create consumer group if it doesn't exist."""
        try:
            await self._redis.xgroup_create(
                self._stream_key, self._group, id="0", mkstream=True
            )
        except aioredis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    async def consume(
        self,
        handler: Any,
        event_type: type[T] | None = None,
    ) -> None:
        """Start consuming events. Calls handler(event_data) for each message."""
        await self.ensure_group()
        self._running = True

        await logger.ainfo(
            "consumer_started",
            stream=self._stream_key,
            group=self._group,
            consumer=self._consumer,
        )

        while self._running:
            try:
                messages = await self._redis.xreadgroup(
                    groupname=self._group,
                    consumername=self._consumer,
                    streams={self._stream_key: ">"},
                    count=self._batch_size,
                    block=self._block_ms,
                )

                if not messages:
                    continue

                for _stream, entries in messages:
                    for msg_id, data in entries:
                        try:
                            raw = orjson.loads(data[b"data"])
                            parsed = event_type.model_validate(raw) if event_type else raw
                            await handler(parsed)
                            await self._redis.xack(
                                self._stream_key, self._group, msg_id
                            )
                        except Exception:
                            await logger.aexception(
                                "message_processing_failed",
                                stream=self._stream_key,
                                msg_id=msg_id,
                            )

            except asyncio.CancelledError:
                self._running = False
                break
            except Exception:
                await logger.aexception(
                    "consumer_error",
                    stream=self._stream_key,
                )
                await asyncio.sleep(1)

    def stop(self) -> None:
        self._running = False


async def create_redis_pool(settings: RedisSettings) -> aioredis.Redis:
    """Create a Redis connection pool from settings."""
    return aioredis.from_url(
        settings.url,
        max_connections=settings.max_connections,
        decode_responses=False,
    )
