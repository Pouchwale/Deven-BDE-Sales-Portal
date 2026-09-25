"""The backend serves the portal's pages (app/web.py) - one service, one origin."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app import web

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"

#: Must match HASHED and sourceHash() in frontend/scripts/export-to-backend.mjs.
HASHED = ["src", "public", "next.config.ts", "package-lock.json", "postcss.config.mjs", "tsconfig.json"]


def _source_hash() -> str:
    paths: list[str] = []
    for entry in HASHED:
        path = FRONTEND / entry
        if path.is_file():
            paths.append(entry)
        elif path.is_dir():
            paths.extend(
                p.relative_to(FRONTEND).as_posix() for p in path.rglob("*") if p.is_file()
            )
    digest = hashlib.sha256()
    for rel in sorted(paths):
        body = (FRONTEND / rel).read_bytes().decode("latin-1").replace("\r\n", "\n")
        digest.update((rel + "\0" + body + "\0").encode("latin-1"))
    return digest.hexdigest()


def test_the_build_is_committed() -> None:
    assert web.available(), "backend/app/web is missing: run `npm run export:backend` in frontend/"


@pytest.mark.skipif(not FRONTEND.is_dir(), reason="frontend source not checked out")
def test_the_build_matches_the_frontend_source() -> None:
    """A frontend change without a rebuild would ship the OLD pages."""
    recorded = (web.WEB_DIR / "SOURCE_HASH").read_text().strip()
    assert recorded == _source_hash(), (
        "The frontend changed since backend/app/web was built. Run "
        "`npm run export:backend` in frontend/ and commit backend/app/web."
    )


def test_root_serves_the_app_with_page_headers(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["content-security-policy"] == web.PAGE_CSP
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize("path", ["/login", "/dashboard", "/customers", "/customers/detail", "/admin/users"])
def test_every_page_is_served(client, path) -> None:
    response = client.get(path)
    assert response.status_code == 200, path
    assert response.headers["content-type"].startswith("text/html")


@pytest.mark.parametrize("path", ["/", "/dashboard", "/customers/detail"])
def test_pages_answer_head(client, path) -> None:
    """The app probes pages with HEAD; a 405 there broke every page."""
    response = client.head(path)
    assert response.status_code == 200, path
    assert response.content == b""


def test_build_assets_are_cached_for_good(client) -> None:
    asset = next((web.WEB_DIR / "_next" / "static").rglob("*.js"))
    response = client.get("/" + asset.relative_to(web.WEB_DIR).as_posix())
    assert response.status_code == 200
    assert "immutable" in response.headers["cache-control"]


def test_every_route_segment_data_file_is_reachable(client) -> None:
    """The client router fetches /x/__next.<group>.x.__PAGE__.txt while you
    navigate; the export stores it as /x/__next.<group>/x/__PAGE__.txt."""
    nested = [
        p for p in web.WEB_DIR.rglob("*.txt")
        if any(part.startswith("__next.") for part in p.relative_to(web.WEB_DIR).parts[:-1])
    ]
    assert nested, "the build has no nested segment files - did the export change?"
    for path in nested:
        parts = path.relative_to(web.WEB_DIR).parts
        i = next(n for n, part in enumerate(parts) if part.startswith("__next."))
        url = "/" + "/".join((*parts[:i], ".".join(parts[i:])))
        response = client.get(url + "?_rsc=abc")
        assert response.status_code == 200, url


def test_old_customer_links_redirect(client) -> None:
    response = client.get("/customers/5f0c7a1e-0000-4000-8000-000000000001", follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == (
        "/customers/detail?id=5f0c7a1e-0000-4000-8000-000000000001"
    )


def test_unknown_page_is_the_portal_404(client) -> None:
    response = client.get("/no-such-page")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")


def test_unknown_api_path_is_still_a_json_404(client) -> None:
    response = client.get("/api/no-such-endpoint")
    assert response.status_code == 404
    assert "error" in response.json()


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_unknown_api_path_is_a_404_for_every_method(client, method) -> None:
    """Not a 405: a removed endpoint must read as gone."""
    response = client.request(method, "/api/leads/x/activities/y/undo")
    assert response.status_code == 404
    assert "error" in response.json()


def test_wrong_method_on_a_real_endpoint_is_still_405(client) -> None:
    assert client.delete("/api/leads/stats").status_code == 405


def test_writing_to_a_page_is_refused(client) -> None:
    assert client.post("/dashboard").status_code == 405


def test_api_and_health_are_not_shadowed(client) -> None:
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize("path", ["../main.py", "../../.env", "..%2Fmain.py", "/etc/passwd"])
def test_nothing_outside_the_build_is_reachable(path) -> None:
    assert web._resolve(path) is None
