"""Transport layer: the per-protocol wire adapters and resilience helpers."""

from .async_base import AsyncConnection, AsyncTransportBase
from .async_pool import (
    AsyncConnectionPool,
    AsyncPooledConnection,
    PoolTimeoutError,
)
from .async_http import AsyncConnectionFactory, AsyncHttpTransport
from .async_retry import call_with_retry_async
from .async_sse import AsyncSSETransport
from .async_websocket import AsyncWebSocket, AsyncWebSocketTransport
from .base import Deadline, Transport
from .cli import CliTransport, Runner
from .graphql import GraphQLTransport, build_query
from .http import Connection, ConnectionFactory, HttpTransport
from .pool import ConnectionPool, PooledConnection
from .retry import (
    BreakerState,
    CircuitBreaker,
    CircuitBreakerOpenError,
    RetryExhaustedError,
    RetryPolicy,
    call_with_retry,
)
from .sse import SSEEvent, SSEParser, SSETransport
from .websocket import WebSocket, WebSocketTransport
from .ws_protocol import FrameError, Request, Response

__all__ = [
    "AsyncConnection",
    "AsyncConnectionPool",
    "AsyncPooledConnection",
    "AsyncConnectionFactory",
    "AsyncHttpTransport",
    "AsyncSSETransport",
    "AsyncWebSocket",
    "AsyncWebSocketTransport",
    "AsyncTransportBase",
    "BreakerState",
    "CircuitBreaker",
    "CircuitBreakerOpenError",
    "CliTransport",
    "Connection",
    "ConnectionFactory",
    "ConnectionPool",
    "Deadline",
    "GraphQLTransport",
    "HttpTransport",
    "PoolTimeoutError",
    "PooledConnection",
    "RetryExhaustedError",
    "RetryPolicy",
    "Runner",
    "SSEEvent",
    "SSEParser",
    "FrameError",
    "Request",
    "Response",
    "SSETransport",
    "Transport",
    "WebSocket",
    "WebSocketTransport",
    "build_query",
    "call_with_retry",
    "call_with_retry_async",
]
