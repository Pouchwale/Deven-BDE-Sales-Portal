"""Errors, security headers, request ids, access logs, health and CORS.

Most checks run against a small app assembled from the SAME pieces as
app.main (error handlers, CORS kwargs, RequestContextMiddleware), so routes
that deliberately explode are never added to the real application.
"""
from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.config import Settings
from app.core.errors import register_error_handlers
from app.core.middleware import RequestContextMiddleware
from app.main import app as real_app, cors_kwargs

SECRET_SQL = "INSERT INTO users (hashed_password) VALUES ('s3cr3t-in-sql')"


def build_app(*, production: bool = False) -> FastAPI:
    config = Settings(
        _env_file=None,
        ENV="development",
        DATABASE_URL="sqlite://",
        CORS_ORIGINS="https://portal.example.com",
        CORS_ORIGIN_REGEX="",
    )
    test_app = FastAPI()
    test_app.add_middleware(CORSMiddleware, **cors_kwargs(config))
    test_app.add_middleware(
        RequestContextMiddleware, production=production, hsts=production
    )
    register_error_handlers(test_app)

    @test_app.get("/api/items/{item_id}")
    def item(item_id: int) -> dict:
        return {"id": item_id}

    @test_app.get("/api/boom")
    def boom() -> dict:
        raise RuntimeError(f"traceback detail {SECRET_SQL}")

    @test_app.get("/api/integrity")
    def integrity() -> dict:
        raise IntegrityError(SECRET_SQL, {"p": "s3cr3t"}, Exception("duplicate key"))

    @test_app.get("/api/operational")
    def operational() -> dict:
        raise OperationalError(SECRET_SQL, {}, Exception("connection refused"))

    @test_app.get("/api/stream")
    def stream() -> StreamingResponse:
        def chunks():
            for i in range(3):
                yield f"data: chunk {i}\n\n"

        return StreamingResponse(chunks(), media_type="text/event-stream")

    return test_app


@pytest.fixture
def local() -> TestClient:
    with TestClient(build_app()) as test_client:
        yield test_client


# ------------------------------------------------------------------ errors
def test_unhandled_exception_is_a_safe_500_envelope(local: TestClient) -> None:
    response = local.get("/api/boom")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["message"] == "Something went wrong. Please try again."
    assert body["error"]["details"]["request_id"] == response.headers["X-Request-ID"]
    text = response.text
    for tell in ("Traceback", "RuntimeError", "s3cr3t", "INSERT", "traceback detail"):
        assert tell not in text
    # Security headers survive the error path too.
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_the_traceback_is_logged_server_side_with_the_request_id(local, caplog) -> None:
    with caplog.at_level(logging.ERROR, logger="app.errors"):
        response = local.get("/api/boom", headers={"X-Request-ID": "trace-me-12345"})
    assert response.status_code == 500
    errors = [r for r in caplog.records if r.name == "app.errors"]
    assert errors and errors[0].exc_info is not None
    assert errors[0].request_id == "trace-me-12345"


def test_integrity_error_is_409_without_sql(local: TestClient) -> None:
    response = local.get("/api/integrity")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"
    assert "INSERT" not in response.text and "s3cr3t" not in response.text
    assert "duplicate key" not in response.text


def test_operational_error_is_503_without_sql(local: TestClient) -> None:
    response = local.get("/api/operational")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert "INSERT" not in response.text and "connection refused" not in response.text


def test_not_found_and_method_not_allowed_use_the_envelope(local: TestClient) -> None:
    missing = local.get("/api/does-not-exist")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"

    wrong = local.delete("/api/items/1")
    assert wrong.status_code == 405
    assert "error" in wrong.json()


def test_validation_errors_do_not_echo_input(client: TestClient) -> None:
    password = "Leaky-Password-123"
    response = client.post("/api/auth/login", json={"email": 12345, "password": [password]})
    assert response.status_code == 422
    assert password not in response.text


# ------------------------------------------------------------------ headers
EXPECTED_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Cache-Control": "no-store",
}


def test_security_headers_on_api_responses(local: TestClient) -> None:
    response = local.get("/api/items/7")
    assert response.status_code == 200
    for name, value in EXPECTED_HEADERS.items():
        assert response.headers.get(name) == value, name
    # HSTS only in production behind HTTPS.
    assert "Strict-Transport-Security" not in response.headers


def test_hsts_in_production() -> None:
    with TestClient(build_app(production=True)) as prod_client:
        response = prod_client.get("/api/items/7")
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"


def test_streaming_responses_still_stream_with_headers(local: TestClient) -> None:
    with local.stream("GET", "/api/stream") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        body = "".join(response.iter_text())
    assert body == "".join(f"data: chunk {i}\n\n" for i in range(3))


def test_real_app_health_has_headers_and_no_secrets(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert set(body) <= {"status", "app", "version", "env"}
    for name, value in EXPECTED_HEADERS.items():
        assert response.headers.get(name) == value, name


# ------------------------------------------------------------------ request id
def test_request_id_is_generated_when_absent(local: TestClient) -> None:
    rid = local.get("/api/items/1").headers["X-Request-ID"]
    assert len(rid) >= 16


def test_safe_inbound_request_id_is_echoed(local: TestClient) -> None:
    response = local.get("/api/items/1", headers={"X-Request-ID": "abc-123_DEF.456"})
    assert response.headers["X-Request-ID"] == "abc-123_DEF.456"


@pytest.mark.parametrize("bad", ["short", "has space in it", "<script>alert(1)</script>", "x" * 65])
def test_unsafe_inbound_request_id_is_replaced(local: TestClient, bad: str) -> None:
    response = local.get("/api/items/1", headers={"X-Request-ID": bad})
    assert response.headers["X-Request-ID"] != bad


# ------------------------------------------------------------------ access log
def test_access_log_uses_the_route_template_and_no_query_string(local, caplog) -> None:
    with caplog.at_level(logging.INFO, logger="app.access"):
        local.get(
            "/api/items/42?email=person@example.com&token=abc",
            headers={"Authorization": "Bearer secret-token", "Cookie": "bde_session=zzz"},
        )
    records = [r for r in caplog.records if r.name == "app.access"]
    assert records
    record = records[-1]
    assert record.route == "/api/items/{item_id}"
    assert record.method == "GET"
    assert record.status == 200
    assert isinstance(record.duration_ms, float)
    rendered = " ".join(str(v) for v in vars(record).values())
    for leak in ("person@example.com", "token=abc", "secret-token", "zzz"):
        assert leak not in rendered


def test_json_log_format_is_one_object_per_line() -> None:
    import json

    from app.core.logging import JsonFormatter

    record = logging.LogRecord("app.access", logging.INFO, __file__, 1, "request", None, None)
    record.request_id = "rid-12345678"
    record.status = 200
    line = JsonFormatter().format(record)
    payload = json.loads(line)
    assert payload["level"] == "INFO"
    assert payload["request_id"] == "rid-12345678"
    assert payload["status"] == 200
    assert payload["timestamp"].endswith("Z")


# ------------------------------------------------------------------ health
def test_health_db_ok(client: TestClient) -> None:
    response = client.get("/api/health/db")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_db_unavailable_reveals_nothing(client: TestClient, monkeypatch) -> None:
    import app.db.session as session_module

    class BrokenEngine:
        dialect = session_module.engine.dialect

        def connect(self):
            raise OperationalError(SECRET_SQL, {}, Exception("password authentication failed"))

    monkeypatch.setattr(session_module, "engine", BrokenEngine())
    response = client.get("/api/health/db")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_health_hides_env_in_production(client: TestClient, monkeypatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "ENV", "production")
    assert "env" not in client.get("/health").json()
    assert "dialect" not in client.get("/api/health/db").json()


# ------------------------------------------------------------------ CORS
def test_cors_rejects_an_unknown_origin(local: TestClient) -> None:
    response = local.options(
        "/api/items/1",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in response.headers


def test_cors_allows_the_configured_origin_with_limited_methods(local: TestClient) -> None:
    response = local.options(
        "/api/items/1",
        headers={
            "Origin": "https://portal.example.com",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "X-CSRF-Token",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://portal.example.com"
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "*" not in response.headers["access-control-allow-methods"]

    exotic = local.options(
        "/api/items/1",
        headers={
            "Origin": "https://portal.example.com",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-Evil-Header",
        },
    )
    assert exotic.status_code == 400


def test_cors_exposes_the_request_id(local: TestClient) -> None:
    response = local.get("/api/items/1", headers={"Origin": "https://portal.example.com"})
    assert "X-Request-ID" in response.headers.get("access-control-expose-headers", "")


def test_docs_are_served_outside_production_only() -> None:
    # The suite runs with ENV=test, so the real app has docs; production
    # construction is covered in test_production_config.
    assert real_app.docs_url == "/docs"
