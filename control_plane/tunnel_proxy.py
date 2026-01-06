from __future__ import annotations

import base64
import os
from urllib.parse import urljoin

from aiohttp import ClientSession, web

CONTROL_URL = os.getenv("EE_CONTROL_URL", "http://127.0.0.1:8088/")
BIND_HOST = os.getenv("EE_PROXY_BIND", "0.0.0.0")
BIND_PORT = int(os.getenv("EE_PROXY_PORT", "9090"))


def resolve_app_name(request: web.Request) -> str | None:
    app_name = request.headers.get("X-EE-App")
    if app_name:
        return app_name
    host = request.headers.get("Host", "")
    if host:
        return host.split(".")[0]
    return None


async def handle_proxy(request: web.Request) -> web.Response:
    app_name = resolve_app_name(request)
    if not app_name:
        return web.json_response({"error": "missing_app"}, status=400)

    proxy_url = urljoin(CONTROL_URL, f"/v1/proxy/{app_name}")
    body = await request.read()
    payload = {
        "method": request.method,
        "path": request.rel_url.raw_path_qs,
        "headers": {k: v for k, v in request.headers.items()},
        "body_b64": base64.b64encode(body).decode("ascii"),
    }
    async with ClientSession() as session:
        async with session.post(proxy_url, json=payload) as resp:
            response_body = await resp.read()
            headers = dict(resp.headers)
            return web.Response(status=resp.status, body=response_body, headers=headers)


def create_app() -> web.Application:
    app = web.Application()
    app.add_routes([web.route("*", "/{tail:.*}", handle_proxy)])
    return app


def main() -> None:
    app = create_app()
    web.run_app(app, host=BIND_HOST, port=BIND_PORT)


if __name__ == "__main__":
    main()
