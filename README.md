# Easy Enclave

[![Dev Pipeline](https://github.com/posix4e/easy-enclave/actions/workflows/pipeline-dev.yml/badge.svg)](https://github.com/posix4e/easy-enclave/actions/workflows/pipeline-dev.yml)

A TDX attestation platform using GitHub as the trust anchor. Deploy workloads to TDX hosts with remote attestation stored as GitHub release attestations.

## Core Concept

**GitHub Repo = Service Identity**: The repo IS the service. Clients connect to a repo, fetch attestations to learn:
1. What measurements to expect (TDX quote in attestation)
2. Where the service endpoint is (URL in attestation metadata)
3. Whether the VM was sealed (sealed flag in attestation)

**Model**: 1 TDX host = 1 GitHub repo = 1 attested service

Single-VM design (current):
- The agent runs inside the TD VM and launches `docker compose` in that same VM.
- The TD VM generates the TDX quote used for attestation.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                      Deployment Initiator                             │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ POST /deploy to agent with bundle artifact                    │   │
│  │ Poll /status for progress and log tails                       │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         TDX Host                                     │
│                                                                      │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │            TD VM (TDX-protected, runs ee-agent)               │  │
│  │                                                                │  │
│  │  POST /deploy ──► Download bundle artifact                     │  │
│  │                      │                                         │  │
│  │                      ▼                                         │  │
│  │  ┌────────────────┐    ┌─────────────────────────────┐         │  │
│  │  │ docker-compose │    │ TDX Quote Generation        │         │  │
│  │  │   workload     │    │ via configfs-tsm            │         │  │
│  │  └────────────────┘    └─────────────────────────────┘         │  │
│  │                                                                │  │
│  │  GET /status ◄── Create GitHub Release with attestation.json   │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
                    │
                    │ Attestation (quote + endpoint)
                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│  GitHub Release                    │  Clients                        │
│  ┌─────────────────────────┐       │                                │
│  │ attestation.json        │ ◄─────│  from easyenclave import connect│
│  │ - TDX quote             │       │  client = connect("owner/repo") │
│  │ - endpoint URL          │       │                                │
│  │ - timestamp             │       │  # Verifies quote via DCAP     │
│  └─────────────────────────┘       │  # Returns verified endpoint   │
└─────────────────────────────────────────────────────────────────────┘
```

## Components

- **GitHub Action** (`action/README.md`) - Bundle-based deploys and inputs
- **Deployment Agent** (`agent/README.md`) - Runtime API and behavior
- **Installer** (`installer/README.md`) - Host setup, VM tooling, and allowlist generation
- **Control Plane** (`control_plane/README.md`) - WS discovery, DCAP enforcement, TTL and routing policy
- **Python SDK** (`sdk/README.md`) - Client verification and usage
- **Examples** (`example/README.md`) - Sample workloads and workflow wiring

## Quick Start

### 1. Set Up TDX Agent

Prerequisites (host):
- BIOS configured for TDX (enable VMX/VT-d and TDX/TME per vendor guidance)
- TDX kernel + libvirt/QEMU with TDX support
- QGS running and enrolled with PCCS or a cloud collateral service

See `installer/README.md` for host setup details. From the repo root:

```bash
sudo ./install-agent.sh
```

Multiple agents per host are supported by running multiple agent VMs with unique
names and host ports. See `installer/README.md` for examples.

### 2. Configure Repository

Add the agent URL as a repository secret:

1. Go to **Settings → Secrets and variables → Actions**
2. Add secret: `AGENT_URL` = `http://your-tdx-host:8000`

### 3. Deploy

Trigger deployment by running a workflow that uses the `./action` composite action:

```bash
# Example: trigger the dev pipeline
gh workflow run pipeline-dev
```

### 4. Connect (Python SDK)

```python
from easyenclave import connect

# Fetches attestation from GitHub, verifies TDX quote
client = connect("your-org/your-repo")
print(f"Verified endpoint: {client.endpoint}")
```

## Bundle Deployment

Details and inputs live in `action/README.md`.

## CI/CD and Releases

```
+-----------------------------+        +------------------------------+
| GitHub Actions              |        | GitHub Releases              |
| - pipeline-dev (main)       |        | - dev allowlist asset         |
| - pipeline-release (v* tag) |        | - deploy-YYYYMMDD-HHMMSS tag  |
|                             |        |   - attestation.json          |
| 1) build allowlist          |        |   - endpoint URL              |
| 2) upload bundle artifact   |        +------------------------------+
| 3) POST /deploy to agent    |                     ^
+--------------+--------------+                     |
               |                                    |
               v                                    |
     +---------------------+             create release
     | Agent VM (control)  |-------------------------+
     | http://host:8000    |
     | runs control plane  |
     +----------+----------+
                |
                | EE_CONTROL_WS (tunnel)
                v
     +---------------------+
     | Agent VM (apps)     |
     | http://host:8001    |
     | runs workloads      |
     +---------------------+
```

Releases:
- `pipeline-dev` updates the `dev` allowlist release and deploys the control plane + examples.
- `pipeline-release` runs on `v*` tags and produces release allowlists.
- Each deployment publishes a `deploy-YYYYMMDD-HHMMSS` release with `attestation.json`
  (quote, endpoint, timestamp, sealed state).

CI/CD lifecycle:
- Release the agent VM image and publish the allowlist asset (`agent-attestation-allowlist.json`).
- For development, `.github/workflows/pipeline-dev.yml` keeps the `dev` allowlist tag up to date and deploys the stack.
- The dev pipeline (`.github/workflows/pipeline-dev.yml`) bakes the agent allowlist, deploys the control plane, and deploys the contacts example.
- The release pipeline (`.github/workflows/pipeline-release.yml`) does the same for `v*` tags.
- Clients verify via the SDK using the latest deployment release attestation.

Notes:
- A single agent VM can only run one workload bundle at a time. For a persistent
  control plane, deploy it on a dedicated agent VM and point other workloads at a
  different agent URL.
- App deploy workflows should pin `agent-release-tag` to a specific agent allowlist release.

Control plane networks:
- Agents register with the control plane over `EE_CONTROL_WS`, providing `repo`, `release_tag`,
  `app_name`, and `network`.
- Networks are policy domains. They are enforced by the control plane (attestation + seal status).
- `forge-1` is sealed-only (production), `sandbox-1` allows unsealed nodes for testing.

## Control Plane + Proxy (Draft)

The control plane accepts outbound WebSocket tunnels from agents, verifies Intel DCAP attestation
against the repo allowlist, and exposes `/v1/resolve/{app}` for proxy routing decisions.
The control plane only accepts sealed agents (`forge-1`).

See `control_plane/README.md` for the protocol and `control_plane/examples/nginx.conf` for a proxy
layout that routes `appname.app.easyenclave.com` only when attestation and health checks are valid.
The proxy listener is part of `control_plane/server.py`.

The production control plane is deployed as an agent-managed workload using
`control_plane/docker-compose.yml`, which runs both prod and staging listeners in
one TD VM (with Caddy terminating TLS).

Minimal run:

```bash
pip install -r control_plane/requirements.txt
python control_plane/server.py
```

Agent tunnel client (built-in):

```bash
EE_CONTROL_WS=ws://127.0.0.1:8088/v1/tunnel \
EE_REPO=owner/repo \
EE_RELEASE_TAG=v0.1.3 \
EE_APP_NAME=myapp \
EE_NETWORK=forge-1 \
EE_BACKEND_URL=http://127.0.0.1:8080 \
python agent/agent.py --host 0.0.0.0 --port 8000
```

## Agent API

The agent exposes a simple HTTP API:

```bash
# The deploy workflow uploads a bundle artifact (docker-compose + public files),
# then passes the artifact ID to the agent along with private env.
# Start deployment
curl -X POST http://agent:8000/deploy \
  -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"repo": "owner/repo", "bundle_artifact_id": 123456, "private_env": "KEY=VALUE\n", "seal_vm": true}'

# Status includes host-side serial and QEMU log tails.
curl http://agent:8000/status/{deployment_id}

# Health check
curl http://agent:8000/health
```

## License

MIT
