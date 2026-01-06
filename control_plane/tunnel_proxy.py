from __future__ import annotations

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

    resolve_url = urljoin(CONTROL_URL, f"/v1/resolve/{app_name}")
    async with ClientSession() as session:
        async with session.get(resolve_url) as resp:
            if resp.status != 200:
                payload = await resp.json()
                return web.json_response(payload, status=resp.status)

    return web.json_response({"error": "tunnel_not_implemented", "app": app_name}, status=501)


def create_app() -> web.Application:
    app = web.Application()
    app.add_routes([web.route("*", "/{tail:.*}", handle_proxy)])
    return app


def main() -> None:
    app = create_app()
    web.run_app(app, host=BIND_HOST, port=BIND_PORT)


if __name__ == "__main__":
    main()
