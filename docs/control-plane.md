---
layout: default
title: control-plane
---

# control plane

the control plane is the network coordinator. it is itself an attested agent that verifies nodes,
routes traffic, and maintains the ledger.

## status

- current: discovery + routing + attestation + health gating over ws tunnels
- draft: usd credit ledger endpoints are implemented but admin-only and subject to change
- planned: full settlement automation, stake gating, and abuse authorization workflow

## responsibilities

- verify node attestation and health
- maintain the authoritative ledger for credits, usage, transfers, and settlement
- route traffic to private agents over outbound ws tunnels
- enforce eligibility (stake + attestation + health)
- authorize abuse reports (control plane owner)

## settlement model (planned)

- users prepay usd credits (1 credit = $1)
- credits lock for a period while compute runs
- settlement is zero tolerance:
  - any missed health check fails the period
  - any missed attestation fails the period
  - any authorized abuse report fails the period
- pass: locked credits transfer to provider
- fail: locked credits return to user

## pricing and routing

- nodes publish a usd price per vcpu-hour
- control plane routes to lowest effective price among eligible nodes, weighted by trust
- prices are posted; no algorithmic price curve

## attestation

- intel dcap verification against allowlisted measurements
- sealed-only policy for production networks
- attestation verification currently relies on pccs/dcap online collateral

## endpoints

### current

| endpoint | description |
|----------|-------------|
| `GET /health` | service health |
| `GET /v1/tunnel` | websocket for agents |
| `GET /v1/resolve/{app}` | proxy routing (public) |
| `POST /v1/proxy/{app}` | forward over ws tunnel |
| `GET /v1/apps` | admin list (auth required) |
| `GET /v1/apps/{app}` | admin detail |

### draft ledger endpoints

- `POST /v1/credits/purchase` - mint usd credits for a user
- `POST /v1/credits/transfer` - transfer credits between accounts
- `GET /v1/balances/{account}` - account balance
- `POST /v1/usage/report` - report usage for a period
- `POST /v1/settlements/{period}/finalize` - settle a period
- `POST /v1/abuse/reports` - file abuse report (launcher)
- `POST /v1/abuse/reports/{id}/authorize` - authorize abuse (owner)
- `POST /v1/nodes/register` - register node capacity, pricing, stake
- `GET /v1/nodes/{node}` - admin node detail

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
EE_LAUNCHER_TOKEN=launcher_secret
EE_UPTIME_TOKEN=uptime_secret

# proxy
EE_PROXY_BIND=0.0.0.0
EE_PROXY_PORT=9090

# ledger
EE_DB_PATH=control_plane/data/control-plane.db
EE_HEALTH_TIMEOUT_SEC=120
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

optional: update cloudflare dns via api (a/aaaa for `control` + `*.app`):

```bash
export CLOUDFLARE_API_TOKEN=...
export CLOUDFLARE_ZONE=easyenclave.com
python control_plane/scripts/cloudflare_dns.py --ip 1.2.3.4 --proxied --dry-run
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

use nginx or another proxy to route based on this response.

if you proxy through cloudflare, use ssl/tls "full (strict)" so it connects to
your origin over https. websockets are supported.

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
