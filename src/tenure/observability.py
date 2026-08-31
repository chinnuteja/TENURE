"""Vendor-neutral OpenTelemetry instrumentation for TENURE control decisions."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
)


def build_tracer(
    exporter: SpanExporter | None = None,
    *,
    service_name: str = "tenure-control-plane",
) -> trace.Tracer:
    """Create an isolated tracer; Cloud OTLP or local exporters can be injected."""
    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": service_name,
                "service.namespace": "tenure",
            }
        )
    )
    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("tenure.authority", "0.1.0")


def build_cloud_tracer(
    project_id: str,
    *,
    exporter: SpanExporter | None = None,
) -> trace.Tracer:
    """Export OTLP spans to Google Telemetry API with Application Default Credentials."""
    if exporter is None:
        try:
            import google.auth
            import google.auth.transport.grpc
            import google.auth.transport.requests
            import grpc
            from google.auth.transport.grpc import AuthMetadataPlugin
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )
        except ImportError as exc:
            raise RuntimeError(
                'Install cloud dependencies with: python -m pip install -e ".[cloud]"'
            ) from exc

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        request = google.auth.transport.requests.Request()
        auth_plugin = AuthMetadataPlugin(credentials=credentials, request=request)
        channel_credentials = grpc.composite_channel_credentials(
            grpc.ssl_channel_credentials(),
            grpc.metadata_call_credentials(auth_plugin),
        )
        exporter = OTLPSpanExporter(
            credentials=channel_credentials,
            endpoint="https://telemetry.googleapis.com:443/v1/traces",
        )

    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": "tenure-control-plane",
                "service.namespace": "tenure",
                "gcp.project_id": project_id,
            }
        )
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    return provider.get_tracer("tenure.authority", "0.1.0")


class TenureTracing:
    def __init__(self, tracer: trace.Tracer | None = None) -> None:
        self.tracer = tracer or trace.get_tracer("tenure.authority", "0.1.0")

    @contextmanager
    def span(self, name: str, **attributes: Any):
        clean_attributes = {
            key: value for key, value in attributes.items() if value is not None
        }
        with self.tracer.start_as_current_span(name, attributes=clean_attributes) as active:
            yield active
