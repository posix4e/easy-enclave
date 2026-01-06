# Easy Enclave Control Plane (Draft)

This component is a control-plane + discovery service that accepts outbound WebSocket tunnels from agents.
It enforces Intel DCAP attestation, allowlist matching per repo/release, and sealed-only policy for prod.

## What it does

- **Registers apps** via WebSocket with `repo`, `release_tag`, and `app_name`.
- **Attests agents** on connect and every 60 minutes by default.
- **Rejects non-sealed** nodes in prod.
- **Tracks TTL** (30 days) with a 3-day warning window.
- **Serves status** for proxy/dashboard via `GET /v1/resolve/{app_name}`.

## Endpoints

- `GET /health` -> service health
- `GET /v1/tunnel` -> WebSocket endpoint for agents
- `GET /v1/resolve/{app_name}` -> public status for proxy (no auth)
- `GET /v1/apps` -> admin list (requires `EE_ADMIN_TOKEN` if set)
- `GET /v1/apps/{app_name}` -> admin detail (requires `EE_ADMIN_TOKEN` if set)

## WebSocket Messages

Register:
```json
{"type":"register","repo":"org/repo","release_tag":"v0.1.3","app_name":"myapp","network":"prod","agent_id":"uuid"}
```

Attestation challenge:
```json
{"type":"attest_request","nonce":"<hex>","deadline_s":30,"reason":"register"}
```

Attestation response:
```json
{"type":"attest_response","nonce":"<hex>","quote":"<base64>","report_data":"<hex>","measurements":{...}}
```

Health update:
```json
{"type":"health","status":"pass"}
```

## Configuration

Set via environment variables:

- `EE_CONTROL_BIND` (default `0.0.0.0`)
- `EE_CONTROL_PORT` (default `8088`)
- `EE_ALLOWLIST_ASSET` (default `agent-attestation-allowlist.json`)
- `EE_GITHUB_TOKEN` (optional, for private allowlist assets)
- `EE_PCCS_URL` (optional, PCCS override for DCAP)
- `EE_ADMIN_TOKEN` (optional, protects `/v1/apps`)
- `EE_ATTEST_INTERVAL_SEC` (default `3600`)
- `EE_ATTEST_DEADLINE_SEC` (default `30`)
- `EE_REGISTRATION_TTL_DAYS` (default `30`)
- `EE_REGISTRATION_WARN_DAYS` (default `3`)

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r control_plane/requirements.txt
python control_plane/server.py
```
