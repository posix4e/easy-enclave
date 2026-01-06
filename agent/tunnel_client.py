#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import base64
import os
import uuid
from urllib.parse import urljoin

from aiohttp import ClientSession, WSMsgType


CONTROL_WS = os.getenv("EE_CONTROL_WS", "ws://127.0.0.1:8088/v1/tunnel")
REPO = os.getenv("EE_REPO", "")
RELEASE_TAG = os.getenv("EE_RELEASE_TAG", "")
APP_NAME = os.getenv("EE_APP_NAME", "")
NETWORK = os.getenv("EE_NETWORK", "prod")
AGENT_ID = os.getenv("EE_AGENT_ID", str(uuid.uuid4()))
ATTEST_URL = os.getenv("EE_ATTEST_URL", "http://127.0.0.1:8000/attestation")
BACKEND_URL = os.getenv("EE_BACKEND_URL", "http://127.0.0.1:8080")
HEALTH_INTERVAL_SEC = int(os.getenv("EE_HEALTH_INTERVAL_SEC", "60"))
RECONNECT_DELAY_SEC = int(os.getenv("EE_RECONNECT_DELAY_SEC", "5"))


async def fetch_attestation(session: ClientSession) -> dict:
    async with session.get(ATTEST_URL) as resp:
        resp.raise_for_status()
        return await resp.json()


async def proxy_request(session: ClientSession, message: dict) -> dict:
    request_id = message.get("request_id")
    method = message.get("method", "GET")
    path = message.get("path", "/")
    headers = message.get("headers") or {}
    body_b64 = message.get("body_b64") or ""
    body = base64.b64decode(body_b64.encode("ascii")) if body_b64 else b""

    url = urljoin(BACKEND_URL, path.lstrip("/"))
    async with session.request(method, url, headers=headers, data=body) as resp:
        response_body = await resp.read()
        return {
            "type": "proxy_response",
            "request_id": request_id,
            "status": resp.status,
            "headers": dict(resp.headers),
            "body_b64": base64.b64encode(response_body).decode("ascii"),
        }


async def health_loop(ws) -> None:
    while not ws.closed:
        await asyncio.sleep(HEALTH_INTERVAL_SEC)
        if ws.closed:
            return
        await ws.send_json({"type": "health", "status": "pass"})


async def run_client() -> None:
    if not REPO or not RELEASE_TAG or not APP_NAME:
        raise SystemExit("EE_REPO, EE_RELEASE_TAG, and EE_APP_NAME are required")

    async with ClientSession() as session:
        async with session.ws_connect(CONTROL_WS) as ws:
            await ws.send_json(
                {
                    "type": "register",
                    "repo": REPO,
                    "release_tag": RELEASE_TAG,
                    "app_name": APP_NAME,
                    "network": NETWORK,
                    "agent_id": AGENT_ID,
                    "tunnel_version": "1",
                }
            )
            asyncio.create_task(health_loop(ws))

            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    payload = msg.json()
                    msg_type = payload.get("type")
                    if msg_type == "attest_request":
                        attestation = await fetch_attestation(session)
                        await ws.send_json(
                            {
                                "type": "attest_response",
                                "nonce": payload.get("nonce"),
                                "quote": attestation.get("quote"),
                                "report_data": attestation.get("report_data"),
                                "measurements": attestation.get("measurements"),
                            }
                        )
                    elif msg_type == "proxy_request":
                        response = await proxy_request(session, payload)
                        await ws.send_json(response)
                        await ws.send_json({"type": "health", "status": "pass"})
                    elif msg_type == "status":
                        state = payload.get("state")
                        reason = payload.get("reason")
                        print(f"status={state} reason={reason}")
                elif msg.type == WSMsgType.ERROR:
                    break


async def main() -> None:
    while True:
        try:
            await run_client()
        except Exception as exc:
            print(f"tunnel_error={exc}")
        await asyncio.sleep(RECONNECT_DELAY_SEC)


if __name__ == "__main__":
    asyncio.run(main())
