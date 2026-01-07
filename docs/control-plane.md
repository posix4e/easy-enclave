---
layout: default
title: control-plane
---

# control plane

Discovery + routing for attested services.

## what it does

```
┌─────────────────────────────────────────────────┐
│              CONTROL PLANE                      │
│                                                 │
│  agents ──WebSocket──> register + attest        │
│                              │                  │
│                              ▼                  │
│                        allowlist check          │
│                        DCAP verify              │
│                        sealed check             │
│                              │                  │
│                              ▼                  │
│  clients ──HTTP──> /v1/resolve/{app} ──> route  │
│                                                 │
└─────────────────────────────────────────────────┘
```

- **registers apps** via WebSocket tunnel
- **attests agents** on connect + every 60 min
- **rejects unsealed** nodes in production networks
- **tracks TTL** (30 days) with expiration warnings
- **routes requests** to verified backends

## endpoints

| endpoint | description |
|----------|-------------|
| `GET /health` | service health |
| `GET /v1/tunnel` | WebSocket for agents |
| `GET /v1/resolve/{app}` | proxy routing (public) |
| `POST /v1/proxy/{app}` | forward over WS tunnel |
| `GET /v1/apps` | admin list (auth required) |
| `GET /v1/apps/{app}` | admin detail |

## websocket protocol

**agent registers:**
```json
{
  "type": "register",
  "repo": "org/repo",
  "release_tag": "v0.1.3",
  "app_name": "myapp",
  "network": "forge-1",
  "agent_id": "uuid"
}
```

**control plane challenges:**
```json
{
  "type": "attest_request",
  "nonce": "<hex>",
  "deadline_s": 30,
  "reason": "register"
}
```

**agent responds:**
```json
{
  "type": "attest_response",
  "nonce": "<hex>",
  "quote": "<base64>",
  "report_data": "<hex>",
  "measurements": {}
}
```

**health heartbeat:**
```json
{"type": "health", "status": "pass"}
```

## networks

| network | policy | use case |
|---------|--------|----------|
| `forge-1` | sealed only | production |
| `sandbox-1` | unsealed ok | development |

## configuration

```bash
# server bind
EE_CONTROL_BIND=0.0.0.0
EE_CONTROL_PORT=8088

# attestation
EE_ALLOWLIST_ASSET=agent-attestation-allowlist.json
EE_PCCS_URL=https://pccs.example.com
EE_ATTEST_INTERVAL_SEC=3600
EE_ATTEST_DEADLINE_SEC=30

# registration
EE_REGISTRATION_TTL_DAYS=30
EE_REGISTRATION_WARN_DAYS=3

# auth
EE_ADMIN_TOKEN=secret
EE_GITHUB_TOKEN=ghp_xxx

# proxy
EE_PROXY_BIND=0.0.0.0
EE_PROXY_PORT=9090
```

## run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r control_plane/requirements.txt
python control_plane/server.py
```

with docker:

```bash
docker compose -f control_plane/docker-compose.yml up --build
```

## proxy setup

the resolve endpoint returns backend status for your proxy:

```bash
curl https://control.easyenclave.com/v1/resolve/myapp
```

```json
{
  "app_name": "myapp",
  "status": "active",
  "endpoint": "wss://...",
  "sealed": true,
  "last_attest": "2024-01-15T10:30:00Z"
}
```

use nginx or caddy to route based on this response.

## agent connection

agents connect outbound (no inbound ports needed):

```bash
EE_CONTROL_WS=wss://control.easyenclave.com/v1/tunnel \
EE_REPO=owner/repo \
EE_RELEASE_TAG=v0.1.3 \
EE_APP_NAME=myapp \
EE_NETWORK=forge-1 \
EE_BACKEND_URL=http://127.0.0.1:8080 \
python agent/agent.py
```

## next

- [examples](/examples) - contact discovery service
- [action](/action) - deployment workflow
- [concepts](/concepts) - trust model
