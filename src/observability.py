"""Prometheus metrics + OpenTelemetry tracing wiring.

Metrics exposed at `/metrics` via the default Prometheus exposition format.
Tracing is attached if `GATEWAY_OTEL_EXPORTER_OTLP_ENDPOINT` is set, using
the OTLP HTTP exporter; otherwise traces are dropped. Both are optional —
missing dependencies degrade to no-ops.
"""

from __future__ import annotations

from collections.abc import Callable

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Histogram,
    generate_latest,
)
from starlette.requests import Request
from starlette.responses import Response

requests_total = Counter(
    "gateway_requests_total",
    "Total /v1/chat/completions requests",
    labelnames=("plan", "country", "outcome"),
)
detection_count = Counter(
    "gateway_detections_total",
    "Total detections produced (post-merge)",
    labelnames=("entity_type", "tier"),
)
pipeline_latency = Histogram(
    "gateway_pipeline_latency_seconds",
    "End-to-end pipeline latency (detect + substitute + forward + reverse)",
    buckets=(0.05, 0.1, 0.2, 0.34, 0.5, 1.0, 2.0, 5.0),
)
detector_latency = Histogram(
    "gateway_detector_latency_seconds",
    "Per-detector latency",
    labelnames=("detector",),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.5, 1.0),
)


async def metrics_endpoint(request: Request) -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def install_tracing(app_name: str, otlp_endpoint: str | None) -> Callable[[], None]:
    """Install OTel tracing if the endpoint is configured. Returns a shutdown fn."""
    if not otlp_endpoint:
        return lambda: None
    try:  # pragma: no cover - exercised in prod only
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        return lambda: None

    provider = TracerProvider(resource=Resource.create({"service.name": app_name}))
    processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{otlp_endpoint}/v1/traces"))
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    return lambda: provider.shutdown()
