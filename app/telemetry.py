"""OpenTelemetry setup for Order Tracker (Homework 4, Q2/Q3).

Exports metrics, logs, and traces for order lookups. The exporter backend is
chosen with environment variables so the same code works for the Q2 console
stage and the Q3 collector pipeline:

- ``OTEL_METRICS_EXPORTER`` / ``OTEL_TRACES_EXPORTER`` / ``OTEL_LOGS_EXPORTER``:
  ``console`` (default) or ``otlp``.
- ``OTEL_EXPORTER_OTLP_ENDPOINT``: OTLP/gRPC endpoint, default
  ``http://otel-collector:4317``. Local runs without a collector keep the
  ``console`` default.
- ``OTEL_METRIC_EXPORT_INTERVAL_MS``: console metric export interval,
  default ``5000`` so ``docker compose logs app`` shows the request metric
  within seconds of a lookup.

The request counter ``http.server.requests`` always carries the ``http.route``
(route template, e.g. ``/api/orders/{order_id}``) and
``http.response.status_code`` attributes.
"""

import logging
import os

from opentelemetry import metrics, trace
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import (
    ConsoleLogExporter,
    SimpleLogRecordProcessor,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

REQUEST_COUNTER = "http.server.requests"
ORDER_LOOKUP_ROUTE = "/api/orders/{order_id}"

logger = logging.getLogger("order_tracker")
tracer = None
meter = None
request_counter = None

_configured = False


def _metric_reader():
    if os.getenv("OTEL_METRICS_EXPORTER", "console") == "otlp":
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter,
        )

        return PeriodicExportingMetricReader(
            OTLPMetricExporter(
                endpoint=os.getenv(
                    "OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317"
                ),
                insecure=True,
            ),
            export_interval_millis=int(
                os.getenv("OTEL_METRIC_EXPORT_INTERVAL_MS", "15000")
            ),
        )
    return PeriodicExportingMetricReader(
        ConsoleMetricExporter(),
        export_interval_millis=int(
            os.getenv("OTEL_METRIC_EXPORT_INTERVAL_MS", "5000")
        ),
    )


def _span_processor():
    if os.getenv("OTEL_TRACES_EXPORTER", "console") == "otlp":
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )

        return SimpleSpanProcessor(
            OTLPSpanExporter(
                endpoint=os.getenv(
                    "OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317"
                ),
                insecure=True,
            )
        )
    return SimpleSpanProcessor(ConsoleSpanExporter())


def _log_processor():
    if os.getenv("OTEL_LOGS_EXPORTER", "console") == "otlp":
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
            OTLPLogExporter,
        )

        return SimpleLogRecordProcessor(
            OTLPLogExporter(
                endpoint=os.getenv(
                    "OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317"
                ),
                insecure=True,
            )
        )
    return SimpleLogRecordProcessor(ConsoleLogExporter())


def configure():
    """Configure global OTel providers once (safe to call repeatedly)."""
    global _configured, tracer, meter, request_counter
    if _configured:
        return
    trace.set_tracer_provider(
        TracerProvider(resource=Resource({"service.name": "order-tracker"}))
    )
    trace.get_tracer_provider().add_span_processor(_span_processor())
    metrics.set_meter_provider(
        MeterProvider(
            resource=Resource({"service.name": "order-tracker"}),
            metric_readers=[_metric_reader()],
        )
    )
    tracer = trace.get_tracer("order-tracker")
    meter = metrics.get_meter("order-tracker")
    request_counter = meter.create_counter(
        REQUEST_COUNTER,
        unit="{request}",
        description="Count of HTTP requests by route and status code.",
    )
    logger_provider = LoggerProvider(
        resource=Resource({"service.name": "order-tracker"})
    )
    logger_provider.add_log_record_processor(_log_processor())
    handler = LoggingHandler(
        level=logging.INFO, logger_provider=logger_provider
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    _configured = True


def shutdown():
    """Shut down providers so background export threads stop cleanly."""
    try:
        metrics.get_meter_provider().shutdown()
    except Exception:
        pass
    try:
        trace.get_tracer_provider().shutdown()
    except Exception:
        pass


def lookup_route_template(method: str, path: str) -> str:
    """Map an order lookup path to its route template."""
    if method == "GET" and path.startswith("/api/orders/") and path.count("/") == 3:
        return ORDER_LOOKUP_ROUTE
    return path
