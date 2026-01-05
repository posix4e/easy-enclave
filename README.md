# Easy Enclave

[![CI](https://github.com/posix4e/easy-enclave/actions/workflows/ci.yml/badge.svg)](https://github.com/posix4e/easy-enclave/actions/workflows/ci.yml)

A TDX attestation platform using GitHub as the trust anchor. Deploy workloads to TDX hosts with remote attestation stored as GitHub release attestations.

## Core Concept

**GitHub Repo = Service Identity**: The repo IS the service. Clients connect to a repo, fetch attestations to learn:
1. What measurements to expect (TDX quote in attestation)
2. Where the service endpoint is (URL in attestation metadata)
3. Whether the VM was sealed (sealed flag in attestation)

**Model**: 1 TDX host = 1 GitHub repo = 1 attested service

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
│  │  ee-agent (HTTP API on port 8000)                              │  │
│  │                                                                │  │
│  │  POST /deploy ──► Download bundle artifact                     │  │
│  │                      │                                         │  │
│  │                      ▼                                         │  │
│  │  ┌─────────────────────────────────────────────────────────┐  │  │
│  │  │            TD VM (TDX-protected)                         │  │  │
│  │  │                                                          │  │  │
│  │  │  ┌────────────────┐    ┌─────────────────────────────┐  │  │  │
│  │  │  │ docker-compose │    │ TDX Quote Generation        │  │  │  │
│  │  │  │   workload     │    │ via configfs-tsm            │  │  │  │
│  │  │  └────────────────┘    └─────────────────────────────┘  │  │  │
│  │  │                                                          │  │  │
│  │  └──────────────────────────────────────────────────────────┘  │  │
│  │                      │                                         │  │
│  │                      ▼                                         │  │
│  │  GET /status ◄── Create GitHub Release with attestation.json  │  │
│  │                                                                │  │
│  └────────────────────────────────────────────────────────────────┘  │
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
- **Deployment Agent** (`agent/README.md`) - Host setup, API, and runtime behavior
- **Python SDK** (`sdk/README.md`) - Client verification and usage
- **Examples** (`example/README.md`) - Sample workloads and workflow wiring

## Quick Start

### 1. Set Up TDX Agent

See `agent/README.md` for host setup and prerequisites.

### 2. Configure Repository

Add the agent URL as a repository secret:

1. Go to **Settings → Secrets and variables → Actions**
2. Add secret: `AGENT_URL` = `http://your-tdx-host:8000`

### 3. Deploy

Trigger deployment by running a workflow that uses the `./action` composite action:

```bash
# Example: trigger a workflow run
gh workflow run deploy
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

## Host Setup

Host requirements and setup steps live in `agent/README.md`.

## Roadmap

### Completed

- [x] GitHub Action with agent-based deployment
- [x] TD VM creation with docker-compose workloads
- [x] TDX quote generation via QGS vsock
- [x] GitHub release creation with attestation
- [x] Python SDK with DCAP verification
- [x] CI/CD pipeline (lint, test, deploy)

### In Progress

- [ ] Full PCCS integration for TCB verification
- [ ] Multi-repo/multi-host support
- [ ] VM lifecycle management (cleanup, resource limits)

### Future

- [ ] Browser extension for visual attestation
- [ ] Dashboard for repo/host registration
- [ ] Discovery service for finding attested services

## Development

```bash
# Install dev dependencies
pip install -e "sdk[dev]"

# Run tests
pytest sdk/tests -v

# Lint
ruff check sdk/ action/src/
mypy sdk/easyenclave
```

## License

MIT
