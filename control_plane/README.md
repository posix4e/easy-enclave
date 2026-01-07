# Easy Enclave Control Plane (Draft)

This component is a control-plane + discovery service that accepts outbound WebSocket tunnels from agents.
It enforces Intel DCAP attestation, allowlist matching per repo/release, and sealed-only policy for sealed networks.

## What it does

- **Registers apps** via WebSocket with `repo`, `release_tag`, and `app_name`.
- **Attests agents** on connect and every 60 minutes by default.
- **Rejects non-sealed** nodes in sealed networks.
- **Tracks TTL** (30 days) with a 3-day warning window.
- **Serves status** for proxy/dashboard via `GET /v1/resolve/{app_name}`.

## Endpoints

- `GET /health` -> service health
- `GET /v1/tunnel` -> WebSocket endpoint for agents
- `GET /v1/resolve/{app_name}` -> public status for proxy (no auth, returns 403 if not allowed)
- `POST /v1/proxy/{app_name}` -> forwards requests over the WS tunnel (proxy use)
- `GET /v1/apps` -> admin list (requires `EE_ADMIN_TOKEN` if set)
- `GET /v1/apps/{app_name}` -> admin detail (requires `EE_ADMIN_TOKEN` if set)

## WebSocket Messages

Register:
```json
{"type":"register","repo":"org/repo","release_tag":"v0.1.3","app_name":"myapp","network":"forge-1","agent_id":"uuid"}
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
- `EE_PROXY_BIND` (default `0.0.0.0`)
- `EE_PROXY_PORT` (default `9090`)

## Run locally

From the repo root:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r control_plane/requirements.txt
python control_plane/server.py
```

Agent tunnel (built into the agent process):

```bash
EE_CONTROL_WS=ws://127.0.0.1:8088/v1/tunnel \
EE_REPO=owner/repo \
EE_RELEASE_TAG=v0.1.3 \
EE_APP_NAME=myapp \
EE_NETWORK=forge-1 \
EE_BACKEND_URL=http://127.0.0.1:8080 \
python agent/agent.py
```

## Proxy example

See `control_plane/examples/nginx.conf` for a basic `app.easyenclave.com` proxy layout that blocks
unattested or expired backends using the resolve endpoint.

## Tunnel proxy

The proxy listener runs inside `control_plane/server.py`. It forwards incoming
requests to `/v1/proxy/{app}` and dispatches them over the active WebSocket
tunnel handled by the agent process.

## Staging vs Production

Production expects `appname.app.easyenclave.com` to route to the proxy port
(default `9090`). Staging uses `appname.sandbox.app.easyenclave.com` on port
`9091`. The provided `control_plane/Caddyfile` configures both.

## Agent Deployment

The control plane is deployed as an agent-managed workload using
`control_plane/docker-compose.yml`. It runs production and staging services in
the same VM and uses Caddy for TLS termination.
## Networks

- `forge-1` (sealed-only, production)
- `sandbox-1` (unsealed allowed, testing)
