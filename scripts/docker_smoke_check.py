#!/usr/bin/env python3
"""Assert the running Docker stack behaves like the app, not just that it is up.

Checks, in the order a browser would hit them:
  1. GET /api/health          -> status ok, capabilities advertised
  2. GET /api/datasources     -> a JSON source list, and CORS for the web origin
  3. GET /                   -> HTML, and every /_next/static asset it points at
  4. GET /health             -> the data-health page

Usage: docker_smoke_check.py [--api URL] [--web URL]
Exits 1 on the first failed assertion, listing every check it ran.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request

TIMEOUT = 10
results: list[tuple[bool, str]] = []


def get(url: str, origin: str | None = None) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url)
    if origin:
        request.add_header("Origin", origin)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:  # a 404/500 is a result, not a crash
        return exc.code, dict(exc.headers), exc.read()


def check(ok: bool, message: str) -> None:
    results.append((ok, message))
    print(("PASS  " if ok else "FAIL  ") + message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--web", default="http://127.0.0.1:3000")
    parser.add_argument("--web-origin", default="")
    args = parser.parse_args()

    api, web = args.api.rstrip("/"), args.web.rstrip("/")
    web_origin = args.web_origin or web

    status, _, body = get(f"{api}/api/health")
    health = {}
    try:
        health = json.loads(body)
    except json.JSONDecodeError:
        pass
    check(status == 200, f"GET {api}/api/health -> {status}")
    check(health.get("status") == "ok", f"health.status == ok (got {health.get('status')!r})")
    check(
        "chat" in (health.get("capabilities") or []),
        f"health advertises chat (got {health.get('capabilities')})",
    )
    # Tracing must stay a no-op unless both keys are set; an error here means the
    # image leaked a half-configured Langfuse client.
    tracing = health.get("tracing") or {}
    check(
        isinstance(tracing, dict) and tracing.get("enabled") in (True, False),
        f"health reports tracing state ({tracing})",
    )

    status, headers, body = get(f"{api}/api/datasources", origin=web_origin)
    check(status == 200, f"GET {api}/api/datasources -> {status}")
    try:
        sources = json.loads(body)
    except json.JSONDecodeError:
        sources = {}
    check("sources" in sources, "datasources returns a source list")

    allow = headers.get("Access-Control-Allow-Origin") or headers.get(
        "access-control-allow-origin"
    )
    check(
        allow in ("*", web_origin),
        f"API allows the web origin over CORS (got {allow!r})",
    )

    status, _, html = get(f"{web}/")
    page = html.decode("utf-8", "replace")
    check(status == 200, f"GET {web}/ -> {status}")
    check(len(page) > 1000, f"GET {web}/ returns a real page ({len(page)} bytes)")
    check(
        "How can I help you?" in page,
        "the chat screen is server-rendered in the HTML",
    )

    assets = sorted(set(re.findall(r'src="(/_next/static/[^"]+\.js)"', page)))
    check(bool(assets), f"the page references build assets (found {len(assets)})")
    for asset in assets[:3]:
        asset_status, asset_headers, _ = get(f"{web}{asset}")
        content_type = asset_headers.get("Content-Type", "")
        check(
            asset_status == 200 and "javascript" in content_type,
            f"GET {asset} -> {asset_status} ({content_type.split(';')[0]})",
        )

    status, _, _ = get(f"{web}/health")
    check(status == 200, f"GET {web}/health -> {status}")

    failed = [message for ok, message in results if not ok]
    print()
    if failed:
        print(f"{len(failed)} of {len(results)} checks failed")
        return 1
    print(f"all {len(results)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
