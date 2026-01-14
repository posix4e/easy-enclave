from __future__ import annotations

import asyncio
import base64
import json
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from aiohttp import web
from easyenclave.ratls import RatlsVerifyResult, match_quote_measurements, verify_ratls_cert

from control_plane.allowlist import AllowlistCache, fetch_allowlist
from control_plane.config import (
    ADMIN_TOKEN,
    ALLOWLIST_ASSET,
    ATTEST_DEADLINE_SEC,
    ATTEST_INTERVAL_SEC,
    BIND_HOST,
    BIND_PORT,
    DB_PATH,
    DNS_APP_WILDCARD,
    DNS_AUTO_IP,
    DNS_CONTROL_DIRECT_HOST,
    DNS_CONTROL_HOST,
    DNS_FAIL_ON_ERROR,
    DNS_HOSTS,
    DNS_IP,
    DNS_IPV6,
    DNS_PROXIED,
    DNS_TTL,
    DNS_UPDATE_ON_START,
    GITHUB_TOKEN,
    HEALTH_TIMEOUT_SEC,
    LAUNCHER_TOKEN,
    PCCS_URL,
    PROXY_BIND,
    PROXY_PORT,
    RATLS_CERT_TTL_SEC,
    RATLS_ENABLED,
    RATLS_REQUIRE_CLIENT_CERT,
    RATLS_SKIP_PCCS,
    REGISTRATION_TTL_DAYS,
    REGISTRATION_WARN_DAYS,
    UPTIME_TOKEN,
)
from control_plane.ledger import LedgerError, LedgerStore, parse_cents, parse_vcpu_hours
from control_plane.policy import AttestationResult, verify_attestation
from control_plane.ratls import build_server_ssl_context, ensure_ratls_material, extract_peer_cert
from control_plane.registry import Registry, RegistryConfig

STATIC_DIR = Path(__file__).resolve().parent / "static"


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


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
    pending_proxy: dict[str, asyncio.Future] = field(default_factory=dict)
    ratls_result: Optional[RatlsVerifyResult] = None

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
        self.ledger = LedgerStore(DB_PATH)
        self._sessions: dict[web.WebSocketResponse, Session] = {}
        self._sessions_by_app: dict[str, Session] = {}
        self._sealed_networks = {"forge-1"}
        self._allowed_networks = {"forge-1"}

    async def handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ratls_result = None
        if RATLS_ENABLED:
            cert_der = extract_peer_cert(request)
            if cert_der or RATLS_REQUIRE_CLIENT_CERT:
                ratls_result = verify_ratls_cert(
                    cert_der,
                    allowlist=None,
                    pccs_url=PCCS_URL,
                    skip_pccs=RATLS_SKIP_PCCS,
                    require_allowlist=False,
                )
                if not ratls_result.verified:
                    log(f"ratls_client_rejected reason={ratls_result.reason}")
                    raise web.HTTPUnauthorized(reason=f"ratls_{ratls_result.reason}")
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)

        session = Session(ws=ws, ratls_result=ratls_result)
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
        elif msg_type == "proxy_response":
            await self._handle_proxy_response(session, payload)
        elif msg_type == "health":
            await self._handle_health(session, payload)
        else:
            await session.ws.send_json({"type": "status", "state": "invalid", "reason": "unknown_message"})

    async def _handle_register(self, session: Session, payload: dict) -> None:
        repo = payload.get("repo")
        release_tag = payload.get("release_tag")
        app_name = payload.get("app_name")
        agent_id = payload.get("agent_id")
        network = payload.get("network") or "forge-1"

        if network not in self._allowed_networks:
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
        self.ledger.ensure_node(agent_id)

        if RATLS_ENABLED and RATLS_REQUIRE_CLIENT_CERT:
            if not session.ratls_result or not session.ratls_result.verified:
                await session.ws.send_json({"type": "status", "state": "invalid", "reason": "ratls_missing"})
                await session.ws.close()
                return
            allowlist = self.allowlist_cache.get(repo, release_tag)
            if allowlist is None:
                try:
                    allowlist = fetch_allowlist(repo, release_tag, ALLOWLIST_ASSET, GITHUB_TOKEN)
                    self.allowlist_cache.put(repo, release_tag, allowlist)
                except Exception as exc:
                    log(f"allowlist_fetch_failed repo={repo} tag={release_tag} error={exc}")
                    await session.ws.send_json(
                        {"type": "status", "state": "invalid", "reason": f"allowlist_fetch_failed:{exc}"}
                    )
                    await session.ws.close()
                    return
            ok, reason = match_quote_measurements(allowlist, session.ratls_result.measurements or {})
            if not ok:
                log(f"ratls_allowlist_mismatch repo={repo} tag={release_tag} reason={reason}")
                await session.ws.send_json({"type": "status", "state": "invalid", "reason": f"ratls_{reason}"})
                await session.ws.close()
                return

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
            if session.agent_id:
                self.ledger.mark_attestation(session.agent_id, "invalid")
                self.ledger.record_node_event(session.agent_id, "attest_miss", result.reason)
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
        self._sessions_by_app[session.app_name] = session
        session.pending_nonce = None
        session.attesting = False
        session.registered = True
        if session.agent_id:
            self.ledger.mark_attestation(session.agent_id, "valid")
            self.ledger.mark_health(session.agent_id, "pass")

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
        if session.agent_id:
            self.ledger.mark_health(session.agent_id, status)

    async def _handle_proxy_response(self, session: Session, payload: dict) -> None:
        request_id = payload.get("request_id")
        if not request_id:
            return
        future = session.pending_proxy.pop(request_id, None)
        if future and not future.done():
            future.set_result(payload)

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
            if session.agent_id:
                self.ledger.record_node_event(session.agent_id, "attest_miss", "timeout")
                self.ledger.mark_attestation(session.agent_id, "invalid")
            await session.ws.send_json({"type": "status", "state": "invalid", "reason": "attestation_timeout"})
            await session.ws.close()

    async def _attest_loop(self, session: Session) -> None:
        while not session.ws.closed:
            await asyncio.sleep(ATTEST_INTERVAL_SEC)
            if session.ws.closed:
                return
            await self._send_attest_request(session, reason="periodic")

    async def health_watchdog(self) -> None:
        while True:
            await asyncio.sleep(HEALTH_TIMEOUT_SEC)
            now = datetime.now(timezone.utc)
            for record in self.registry.list_apps():
                if not record.ws_connected:
                    continue
                last_health = record.last_health_at or record.registered_at
                if (now - last_health).total_seconds() <= HEALTH_TIMEOUT_SEC:
                    continue
                if record.health_status == "fail":
                    continue
                self.registry.mark_health(record.app_name, "fail")
                if record.agent_id:
                    self.ledger.record_node_event(record.agent_id, "health_miss", "timeout")
                    self.ledger.mark_health(record.agent_id, "fail")

    async def _verify_session_attestation(self, session: Session, attestation: dict) -> AttestationResult:
        allowlist = self.allowlist_cache.get(session.repo, session.release_tag)
        if not allowlist:
            try:
                allowlist = fetch_allowlist(session.repo, session.release_tag, ALLOWLIST_ASSET, GITHUB_TOKEN)
            except Exception as exc:
                return AttestationResult(False, f"allowlist_fetch_failed:{exc}", False)
            self.allowlist_cache.put(session.repo, session.release_tag, allowlist)

        require_sealed = session.network in self._sealed_networks
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
        if session.agent_id:
            self.ledger.mark_attestation(session.agent_id, "invalid")
            self.ledger.record_node_event(session.agent_id, "attest_miss", result.reason)
        return result

    async def _handle_disconnect(self, session: Session) -> None:
        if not session.app_name:
            return
        self.registry.mark_connection(session.app_name, False, session.tunnel_id)
        self.registry.mark_health(session.app_name, "fail")
        if session.agent_id:
            self.ledger.record_node_event(session.agent_id, "health_miss", "disconnect")
            self.ledger.mark_health(session.agent_id, "fail")
        if self._sessions_by_app.get(session.app_name) is session:
            self._sessions_by_app.pop(session.app_name, None)

    def get_session(self, app_name: str) -> Optional[Session]:
        return self._sessions_by_app.get(app_name)

    async def proxy_request(
        self,
        app_name: str,
        method: str,
        path: str,
        headers: dict,
        body: bytes,
    ) -> tuple[int, dict[str, str], bytes]:
        record = self.registry.get(app_name)
        if not record:
            return _json_error(404, {"allowed": False, "reason": "unknown_app"})
        payload = self.registry.status_payload(record)
        if not payload.get("allowed"):
            return _json_error(403, payload)

        session = self.get_session(app_name)
        if not session or session.ws.closed:
            return _json_error(503, {"allowed": False, "reason": "no_tunnel"})

        request_id = secrets.token_hex(12)
        future = asyncio.get_running_loop().create_future()
        session.pending_proxy[request_id] = future

        headers = {k: v for k, v in headers.items() if k.lower() != "host"}
        await session.ws.send_json(
            {
                "type": "proxy_request",
                "request_id": request_id,
                "method": method,
                "path": path,
                "headers": headers,
                "body_b64": base64.b64encode(body).decode("ascii"),
            }
        )

        try:
            response_payload = await asyncio.wait_for(future, timeout=15)
        except asyncio.TimeoutError:
            session.pending_proxy.pop(request_id, None)
            return _json_error(504, {"allowed": False, "reason": "proxy_timeout"})

        status = int(response_payload.get("status", 502))
        body_b64 = response_payload.get("body_b64") or ""
        response_body = base64.b64decode(body_b64.encode("ascii")) if body_b64 else b""
        response_headers = response_payload.get("headers") or {}
        return status, response_headers, response_body


async def require_admin(request: web.Request) -> None:
    if not ADMIN_TOKEN:
        return
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {ADMIN_TOKEN}":
        raise web.HTTPUnauthorized()


def _bearer_token(request: web.Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth.split(" ", 1)[1]
    return ""


async def require_launcher(request: web.Request) -> None:
    token = _bearer_token(request)
    if ADMIN_TOKEN and token == ADMIN_TOKEN:
        return
    if not LAUNCHER_TOKEN:
        return
    if token != LAUNCHER_TOKEN:
        raise web.HTTPUnauthorized()


async def require_uptime(request: web.Request) -> None:
    token = _bearer_token(request)
    if ADMIN_TOKEN and token == ADMIN_TOKEN:
        return
    if not UPTIME_TOKEN:
        return
    if token != UPTIME_TOKEN:
        raise web.HTTPUnauthorized()


def register_control_routes(
    app: web.Application,
    control: ControlPlane,
    *,
    include_public: bool = True,
    include_admin: bool = True,
    include_ws: bool = True,
    include_health: bool = True,
) -> None:
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

    async def proxy_app(request: web.Request) -> web.Response:
        app_name = request.match_info["app_name"]

        try:
            incoming = await request.json()
        except Exception:
            return web.json_response({"allowed": False, "reason": "invalid_proxy_payload"}, status=400)
        method = incoming.get("method", "GET")
        path = incoming.get("path", "/")
        headers = incoming.get("headers") or {}
        body_b64 = incoming.get("body_b64") or ""
        body = base64.b64decode(body_b64.encode("ascii")) if body_b64 else b""
        status, response_headers, response_body = await control.proxy_request(
            app_name,
            method,
            path,
            headers,
            body,
        )
        return web.Response(status=status, body=response_body, headers=response_headers)

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

    async def admin_page(request: web.Request) -> web.Response:
        await require_admin(request)
        return web.FileResponse(STATIC_DIR / "admin.html")

    async def purchase_credits(request: web.Request) -> web.Response:
        await require_admin(request)
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "invalid_json"}, status=400)
        account_id = payload.get("account_id")
        amount = payload.get("amount_usd") or payload.get("amount")
        if not account_id:
            return web.json_response({"error": "missing_account_id"}, status=400)
        try:
            amount_cents = parse_cents(amount)
            result = control.ledger.purchase_credits(account_id, amount_cents)
        except LedgerError as exc:
            return web.json_response({"error": exc.reason}, status=400)
        return web.json_response({"account_id": account_id, "balance_cents": result["balance_cents"]})

    async def transfer_credits(request: web.Request) -> web.Response:
        await require_admin(request)
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "invalid_json"}, status=400)
        from_account = payload.get("from_account")
        to_account = payload.get("to_account")
        amount = payload.get("amount_usd") or payload.get("amount")
        if not from_account or not to_account:
            return web.json_response({"error": "missing_account"}, status=400)
        try:
            amount_cents = parse_cents(amount)
            result = control.ledger.transfer_credits(from_account, to_account, amount_cents)
        except LedgerError as exc:
            return web.json_response({"error": exc.reason}, status=400)
        return web.json_response(result)

    async def get_balance(request: web.Request) -> web.Response:
        await require_admin(request)
        account_id = request.match_info["account_id"]
        result = control.ledger.get_balance(account_id)
        return web.json_response(result)

    async def report_usage(request: web.Request) -> web.Response:
        await require_uptime(request)
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "invalid_json"}, status=400)
        account_id = payload.get("account_id")
        node_id = payload.get("node_id")
        period_start = payload.get("period_start")
        period_end = payload.get("period_end")
        vcpu_hours = payload.get("vcpu_hours")
        if not all([account_id, node_id, period_start, period_end, vcpu_hours]):
            return web.json_response({"error": "missing_fields"}, status=400)
        try:
            hours = parse_vcpu_hours(vcpu_hours)
            result = control.ledger.report_usage(account_id, node_id, hours, period_start, period_end)
        except LedgerError as exc:
            return web.json_response({"error": exc.reason}, status=400)
        return web.json_response(result)

    async def finalize_settlement(request: web.Request) -> web.Response:
        await require_admin(request)
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "invalid_json"}, status=400)
        node_id = payload.get("node_id")
        period_start = payload.get("period_start")
        period_end = payload.get("period_end")
        if not all([node_id, period_start, period_end]):
            return web.json_response({"error": "missing_fields"}, status=400)
        try:
            result = control.ledger.settle_period(node_id, period_start, period_end)
        except LedgerError as exc:
            return web.json_response({"error": exc.reason}, status=400)
        return web.json_response(result)

    async def file_abuse_report(request: web.Request) -> web.Response:
        await require_launcher(request)
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "invalid_json"}, status=400)
        node_id = payload.get("node_id")
        period_start = payload.get("period_start")
        period_end = payload.get("period_end")
        reason = payload.get("reason")
        reported_by = payload.get("reported_by") or "launcher"
        if not node_id:
            return web.json_response({"error": "missing_node_id"}, status=400)
        try:
            result = control.ledger.file_abuse_report(node_id, period_start, period_end, reported_by, reason)
        except LedgerError as exc:
            return web.json_response({"error": exc.reason}, status=400)
        return web.json_response(result)

    async def authorize_abuse(request: web.Request) -> web.Response:
        await require_admin(request)
        report_id = request.match_info["report_id"]
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "invalid_json"}, status=400)
        action = payload.get("action", "authorize")
        authorized_by = payload.get("authorized_by") or "owner"
        try:
            result = control.ledger.authorize_abuse_report(report_id, authorized_by, action)
        except LedgerError as exc:
            return web.json_response({"error": exc.reason}, status=400)
        return web.json_response(result)

    async def register_node(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "invalid_json"}, status=400)
        node_id = payload.get("node_id")
        if not node_id:
            return web.json_response({"error": "missing_node_id"}, status=400)
        token = _bearer_token(request)
        is_admin = not ADMIN_TOKEN or token == ADMIN_TOKEN
        allow_update = bool(payload.get("allow_update"))
        if not is_admin:
            if not control.ledger.verify_node_token(node_id, token):
                raise web.HTTPUnauthorized()
            allow_update = True
        price_value = payload.get("price_usd_per_vcpu_hour") or payload.get("price_usd")
        stake_value = payload.get("stake_amount_usd")
        stake_tier = payload.get("stake_tier")
        rotate_token = bool(payload.get("rotate_token")) if is_admin else False
        price_cents = parse_cents(price_value) if price_value is not None else None
        stake_cents = parse_cents(stake_value) if stake_value is not None else None
        try:
            result = control.ledger.register_node(
                node_id=node_id,
                price_cents_per_vcpu_hour=price_cents,
                stake_tier=stake_tier,
                stake_amount_cents=stake_cents,
                allow_update=allow_update,
                rotate_token=rotate_token,
            )
        except LedgerError as exc:
            return web.json_response({"error": exc.reason}, status=400)
        return web.json_response(result)

    async def get_node(request: web.Request) -> web.Response:
        await require_admin(request)
        node_id = request.match_info["node_id"]
        node = control.ledger.get_node(node_id)
        if not node:
            raise web.HTTPNotFound()
        return web.json_response(node)

    if include_public:
        routes: list[web.RouteDef] = []
        if include_health:
            routes.append(web.get("/health", health))
        routes.extend(
            [
                web.get("/v1/resolve/{app_name}", resolve_app),
                web.post("/v1/proxy/{app_name}", proxy_app),
                web.post("/v1/usage/report", report_usage),
                web.post("/v1/abuse/reports", file_abuse_report),
                web.post("/v1/nodes/register", register_node),
            ]
        )
        if include_ws:
            routes.append(web.get("/v1/tunnel", control.handle_ws))
        app.add_routes(routes)

    if include_admin:
        app.add_routes(
            [
                web.get("/v1/apps", list_apps),
                web.get("/v1/apps/{app_name}", get_app),
                web.get("/dashboard", dashboard),
                web.post("/v1/credits/purchase", purchase_credits),
                web.post("/v1/credits/transfer", transfer_credits),
                web.get("/v1/balances/{account_id}", get_balance),
                web.post("/v1/settlements/{period}/finalize", finalize_settlement),
                web.post("/v1/abuse/reports/{report_id}/authorize", authorize_abuse),
                web.get("/v1/nodes/{node_id}", get_node),
                web.get("/admin", admin_page),
            ]
        )


def create_app(control: ControlPlane) -> web.Application:
    app = web.Application()
    register_control_routes(app, control, include_public=True, include_admin=True, include_ws=True, include_health=True)
    return app


def _json_error(status: int, payload: dict) -> tuple[int, dict[str, str], bytes]:
    return status, {"Content-Type": "application/json"}, json.dumps(payload).encode()


def resolve_app_name(request: web.Request) -> str | None:
    app_name = request.headers.get("X-EE-App")
    if app_name:
        return app_name
    host = request.headers.get("Host", "")
    if host:
        return host.split(".")[0]
    return None


def create_proxy_app(control: ControlPlane) -> web.Application:
    async def handle_proxy(request: web.Request) -> web.Response:
        app_name = resolve_app_name(request)
        if not app_name:
            return web.json_response({"error": "missing_app"}, status=400)

        body = await request.read()
        status, response_headers, response_body = await control.proxy_request(
            app_name,
            request.method,
            request.rel_url.raw_path_qs,
            dict(request.headers),
            body,
        )
        return web.Response(status=status, body=response_body, headers=response_headers)

    proxy_app = web.Application()
    proxy_app.add_routes([web.route("*", "/{tail:.*}", handle_proxy)])
    return proxy_app


def run_dns_update() -> None:
    if not DNS_UPDATE_ON_START:
        return
    if not DNS_AUTO_IP and not DNS_IP and not DNS_IPV6:
        raise RuntimeError("dns_update_missing_ip")

    script_path = Path(__file__).resolve().parent.parent / "action" / "cloudflare_dns.py"
    cmd = [sys.executable, str(script_path)]
    if DNS_IP:
        cmd.extend(["--ip", DNS_IP])
    elif DNS_AUTO_IP:
        cmd.append("--auto-ip")
    if DNS_IPV6:
        cmd.extend(["--ipv6", DNS_IPV6])
    if DNS_PROXIED:
        cmd.append("--proxied")
    else:
        cmd.append("--dns-only")
    cmd.extend(["--ttl", str(DNS_TTL)])
    if DNS_HOSTS:
        cmd.extend(["--hosts", DNS_HOSTS])
    else:
        cmd.extend(["--control-host", DNS_CONTROL_HOST])
        cmd.extend(["--control-direct-host", DNS_CONTROL_DIRECT_HOST])
        cmd.extend(["--app-wildcard", DNS_APP_WILDCARD])

    print("Updating Cloudflare DNS...")
    subprocess.run(cmd, check=True)


async def _run_servers() -> None:
    control = ControlPlane()
    asyncio.create_task(control.health_watchdog())
    control_app = create_app(control)
    control_runner = web.AppRunner(control_app)
    await control_runner.setup()
    ssl_context = None
    if RATLS_ENABLED:
        ratls_material = ensure_ratls_material("easyenclave-control-plane", RATLS_CERT_TTL_SEC)
        ssl_context = build_server_ssl_context(ratls_material, RATLS_REQUIRE_CLIENT_CERT)
    control_site = web.TCPSite(control_runner, BIND_HOST, BIND_PORT, ssl_context=ssl_context)
    await control_site.start()

    proxy_app = create_proxy_app(control)
    proxy_runner = web.AppRunner(proxy_app)
    await proxy_runner.setup()
    proxy_site = web.TCPSite(proxy_runner, PROXY_BIND, PROXY_PORT)
    await proxy_site.start()

    while True:
        await asyncio.sleep(3600)


def main() -> None:
    if DNS_UPDATE_ON_START:
        try:
            run_dns_update()
        except Exception as exc:
            print(f"error: dns_update_failed: {exc}", file=sys.stderr)
            if DNS_FAIL_ON_ERROR:
                raise SystemExit(1)
    asyncio.run(_run_servers())


if __name__ == "__main__":
    main()
