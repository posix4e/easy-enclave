from __future__ import annotations

import asyncio
import json
import secrets
import time
from dataclasses import dataclass
from typing import Optional

from aiohttp import web

from control_plane.allowlist import AllowlistCache, fetch_allowlist
from control_plane.config import (
    ADMIN_TOKEN,
    ALLOWLIST_ASSET,
    ATTEST_DEADLINE_SEC,
    ATTEST_INTERVAL_SEC,
    BIND_HOST,
    BIND_PORT,
    GITHUB_TOKEN,
    PCCS_URL,
    REGISTRATION_TTL_DAYS,
    REGISTRATION_WARN_DAYS,
)
from control_plane.policy import AttestationResult, verify_attestation
from control_plane.registry import Registry, RegistryConfig


@dataclass
class Session:
    ws: web.WebSocketResponse
    app_name: Optional[str] = None
    repo: Optional[str] = None
    release_tag: Optional[str] = None
    network: str = "prod"
    agent_id: Optional[str] = None
    tunnel_id: Optional[str] = None
    pending_nonce: Optional[str] = None
    pending_sent_at: float = 0.0
    registered: bool = False
    attesting: bool = False

    def info(self) -> dict:
        return {
            "app_name": self.app_name,
            "repo": self.repo,
            "release_tag": self.release_tag,
            "network": self.network,
            "agent_id": self.agent_id,
            "tunnel_id": self.tunnel_id,
        }


class ControlPlane:
    def __init__(self) -> None:
        self.registry = Registry(
            RegistryConfig(ttl_days=REGISTRATION_TTL_DAYS, warn_days=REGISTRATION_WARN_DAYS)
        )
        self.allowlist_cache = AllowlistCache()
        self._sessions: dict[web.WebSocketResponse, Session] = {}

    async def handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)

        session = Session(ws=ws)
        self._sessions[ws] = session

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    await self._handle_ws_message(session, msg.data)
                elif msg.type == web.WSMsgType.ERROR:
                    break
        finally:
            await self._handle_disconnect(session)
            self._sessions.pop(ws, None)

        return ws

    async def _handle_ws_message(self, session: Session, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            await session.ws.send_json({"type": "status", "state": "invalid", "reason": "invalid_json"})
            return

        msg_type = payload.get("type")
        if msg_type == "register":
            await self._handle_register(session, payload)
        elif msg_type == "attest_response":
            await self._handle_attest_response(session, payload)
        elif msg_type == "health":
            await self._handle_health(session, payload)
        else:
            await session.ws.send_json({"type": "status", "state": "invalid", "reason": "unknown_message"})

    async def _handle_register(self, session: Session, payload: dict) -> None:
        repo = payload.get("repo")
        release_tag = payload.get("release_tag")
        app_name = payload.get("app_name")
        agent_id = payload.get("agent_id")
        network = payload.get("network") or "prod"

        if network not in {"prod", "staging", "dev"}:
            await session.ws.send_json({"type": "status", "state": "invalid", "reason": "invalid_network"})
            return
        if not all([repo, release_tag, app_name, agent_id]):
            await session.ws.send_json({"type": "status", "state": "invalid", "reason": "missing_fields"})
            return

        session.repo = repo
        session.release_tag = release_tag
        session.app_name = app_name
        session.agent_id = agent_id
        session.network = network
        session.tunnel_id = f"{app_name}:{secrets.token_hex(8)}"

        await self._send_attest_request(session, reason="register")

    async def _handle_attest_response(self, session: Session, payload: dict) -> None:
        if not session.pending_nonce:
            await session.ws.send_json({"type": "status", "state": "invalid", "reason": "unexpected_attestation"})
            return
        if payload.get("nonce") != session.pending_nonce:
            await session.ws.send_json({"type": "status", "state": "invalid", "reason": "nonce_mismatch"})
            await session.ws.close()
            return

        if time.monotonic() - session.pending_sent_at > ATTEST_DEADLINE_SEC:
            await session.ws.send_json({"type": "status", "state": "invalid", "reason": "attestation_timeout"})
            await session.ws.close()
            return

        attestation = {
            "quote": payload.get("quote"),
            "report_data": payload.get("report_data"),
            "measurements": payload.get("measurements"),
        }
        result = await self._verify_session_attestation(session, attestation)
        if not result.verified:
            await session.ws.send_json({"type": "status", "state": "invalid", "reason": result.reason})
            await session.ws.close()
            return

        self.registry.register(
            app_name=session.app_name,
            repo=session.repo,
            release_tag=session.release_tag,
            network=session.network,
            agent_id=session.agent_id,
        )
        self.registry.mark_attested(session.app_name, result.sealed, "valid")
        self.registry.mark_connection(session.app_name, True, session.tunnel_id)
        session.pending_nonce = None
        session.attesting = False
        session.registered = True

        await session.ws.send_json({"type": "status", "state": "ok", "reason": "attested"})

        asyncio.create_task(self._attest_loop(session))

    async def _handle_health(self, session: Session, payload: dict) -> None:
        if not session.registered:
            await session.ws.send_json({"type": "status", "state": "invalid", "reason": "not_registered"})
            return
        status = payload.get("status", "pass")
        if status not in {"pass", "fail"}:
            status = "fail"
        self.registry.mark_health(session.app_name, status)

    async def _send_attest_request(self, session: Session, reason: str) -> None:
        if session.attesting:
            return
        session.attesting = True
        nonce = secrets.token_hex(16)
        session.pending_nonce = nonce
        session.pending_sent_at = time.monotonic()
        await session.ws.send_json(
            {"type": "attest_request", "nonce": nonce, "deadline_s": ATTEST_DEADLINE_SEC, "reason": reason}
        )
        asyncio.create_task(self._attestation_timeout(session, nonce))

    async def _attestation_timeout(self, session: Session, nonce: str) -> None:
        await asyncio.sleep(ATTEST_DEADLINE_SEC)
        if session.pending_nonce == nonce:
            await session.ws.send_json({"type": "status", "state": "invalid", "reason": "attestation_timeout"})
            await session.ws.close()

    async def _attest_loop(self, session: Session) -> None:
        while not session.ws.closed:
            await asyncio.sleep(ATTEST_INTERVAL_SEC)
            if session.ws.closed:
                return
            await self._send_attest_request(session, reason="periodic")

    async def _verify_session_attestation(self, session: Session, attestation: dict) -> AttestationResult:
        allowlist = self.allowlist_cache.get(session.repo, session.release_tag)
        if not allowlist:
            try:
                allowlist = fetch_allowlist(session.repo, session.release_tag, ALLOWLIST_ASSET, GITHUB_TOKEN)
            except Exception as exc:
                return AttestationResult(False, f"allowlist_fetch_failed:{exc}", False)
            self.allowlist_cache.put(session.repo, session.release_tag, allowlist)

        require_sealed = session.network == "prod"
        result = verify_attestation(attestation, allowlist, require_sealed, PCCS_URL)
        if result.verified:
            return result

        self.registry.register(
            app_name=session.app_name,
            repo=session.repo,
            release_tag=session.release_tag,
            network=session.network,
            agent_id=session.agent_id,
        )
        self.registry.mark_attested(session.app_name, result.sealed, "invalid")
        return result

    async def _handle_disconnect(self, session: Session) -> None:
        if not session.app_name:
            return
        self.registry.mark_connection(session.app_name, False, session.tunnel_id)


async def require_admin(request: web.Request) -> None:
    if not ADMIN_TOKEN:
        return
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {ADMIN_TOKEN}":
        raise web.HTTPUnauthorized()


def create_app(control: ControlPlane) -> web.Application:
    app = web.Application()

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def list_apps(request: web.Request) -> web.Response:
        await require_admin(request)
        payload = [control.registry.status_payload(app) for app in control.registry.list_apps()]
        return web.json_response({"apps": payload})

    async def get_app(request: web.Request) -> web.Response:
        await require_admin(request)
        app_name = request.match_info["app_name"]
        record = control.registry.get(app_name)
        if not record:
            raise web.HTTPNotFound()
        return web.json_response(control.registry.status_payload(record))

    async def resolve_app(request: web.Request) -> web.Response:
        app_name = request.match_info["app_name"]
        record = control.registry.get(app_name)
        if not record:
            return web.json_response({"allowed": False, "reason": "unknown_app"}, status=404)
        payload = control.registry.status_payload(record)
        if not payload.get("allowed"):
            return web.json_response(payload, status=403)
        return web.json_response(payload)

    async def dashboard(request: web.Request) -> web.Response:
        await require_admin(request)
        rows = []
        for record in control.registry.list_apps():
            payload = control.registry.status_payload(record)
            rows.append(
                "<tr>"
                f"<td>{payload['app_name']}</td>"
                f"<td>{payload['repo']}</td>"
                f"<td>{payload['release_tag']}</td>"
                f"<td>{payload['network']}</td>"
                f"<td>{payload['registration_state']}</td>"
                f"<td>{payload['attestation_status']}</td>"
                f"<td>{payload['health_status']}</td>"
                f"<td>{'yes' if payload['sealed'] else 'no'}</td>"
                f"<td>{'yes' if payload['ws_connected'] else 'no'}</td>"
                f"<td>{payload['registration_expires_at']}</td>"
                "</tr>"
            )
        body = "".join(rows) or "<tr><td colspan='10'>No apps registered</td></tr>"
        html = (
            "<!doctype html>"
            "<html><head><meta charset='utf-8'><title>Easy Enclave Dashboard</title>"
            "<style>body{font-family:Arial,Helvetica,sans-serif;margin:24px;}"
            "table{border-collapse:collapse;width:100%;}"
            "th,td{border:1px solid #ddd;padding:8px;text-align:left;}"
            "th{background:#f2f2f2;}</style></head><body>"
            "<h1>Easy Enclave Dashboard</h1>"
            "<table><thead><tr>"
            "<th>App</th><th>Repo</th><th>Release</th><th>Network</th>"
            "<th>TTL</th><th>Attestation</th><th>Health</th><th>Sealed</th>"
            "<th>Connected</th><th>Expires</th>"
            "</tr></thead><tbody>"
            f"{body}"
            "</tbody></table></body></html>"
        )
        return web.Response(text=html, content_type="text/html")

    app.add_routes(
        [
            web.get("/health", health),
            web.get("/v1/apps", list_apps),
            web.get("/v1/apps/{app_name}", get_app),
            web.get("/v1/resolve/{app_name}", resolve_app),
            web.get("/v1/tunnel", control.handle_ws),
            web.get("/dashboard", dashboard),
        ]
    )
    return app


def main() -> None:
    control = ControlPlane()
    app = create_app(control)
    web.run_app(app, host=BIND_HOST, port=BIND_PORT)


if __name__ == "__main__":
    main()
