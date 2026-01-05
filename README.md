# Easy Enclave

[![CI](https://github.com/posix4e/easy-enclave/actions/workflows/ci.yml/badge.svg)](https://github.com/posix4e/easy-enclave/actions/workflows/ci.yml)

A TDX attestation platform using GitHub as the trust anchor. Deploy workloads to TDX hosts with remote attestation stored as GitHub release attestations.

## Core Concept

**GitHub Repo = Service Identity**: The repo IS the service. Clients connect to a repo, fetch attestations to learn:
1. What measurements to expect (TDX quote in attestation)
2. Where the service endpoint is (URL in attestation metadata)

**Model**: 1 TDX host = 1 GitHub repo = 1 attested service

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    GitHub Actions (Standard Runners)                 │
│                                                                      │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────────────┐ │
│  │   CI Job     │   │   CI Job     │   │      CD Job              │ │
│  │   (Lint)     │──►│   (Test)     │──►│   (Deploy via Agent)     │ │
│  │ - ruff       │   │ - pytest     │   │                          │ │
│  │ - mypy       │   │ - SDK tests  │   │ POST /deploy ────────────┼─┼──┐
│  └──────────────┘   └──────────────┘   │ Poll /status             │ │  │
│                                        └──────────────────────────┘ │  │
└─────────────────────────────────────────────────────────────────────┘  │
                                                                         │
    ┌────────────────────────────────────────────────────────────────────┘
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

- **GitHub Action** (`./action`) - Triggers deployment via remote agent, creates attested releases
- **Deployment Agent** (`agent/`) - Runs on TDX host, manages TD VMs and attestation
- **Python SDK** (`sdk/`) - Client library: `connect("owner/repo")` with DCAP verification

## Quick Start

### 1. Set Up TDX Agent

On your TDX-capable host:

```bash
# Clone the repo
git clone https://github.com/posix4e/easy-enclave.git
cd easy-enclave

# Install the agent
sudo ./agent/install.sh

# Verify it's running
sudo systemctl status ee-agent
```

### 2. Configure Repository

Add the agent URL as a repository secret:

1. Go to **Settings → Secrets and variables → Actions**
2. Add secret: `AGENT_URL` = `http://your-tdx-host:8000`

### 3. Deploy

The deployment happens automatically when CI passes on main, or manually via workflow dispatch:

```bash
# Trigger manual deployment
gh workflow run deploy
```

### 4. Connect (Python SDK)

```python
from easyenclave import connect

# Fetches attestation from GitHub, verifies TDX quote
client = connect("your-org/your-repo")
print(f"Verified endpoint: {client.endpoint}")
```

## GitHub Workflows

### CI Workflow (`.github/workflows/ci.yml`)

Runs on every push/PR:
- **Lint**: ruff + mypy
- **Test**: pytest SDK tests
- **Build**: Validate docker-compose, build SDK package

### Deploy Workflow (`.github/workflows/deploy.yml`)

Runs after CI passes on main (or manually):
- Triggers remote agent deployment
- Creates GitHub release with attestation
- Verifies deployment with SDK

## Agent API

The agent exposes a simple HTTP API:

```bash
# The deploy workflow uploads a bundle artifact with docker-compose and public files,
# then passes the artifact ID to the agent.
# Start deployment
curl -X POST http://agent:8000/deploy \
  -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"repo": "owner/repo", "bundle_artifact_id": 123456, "private_env": "KEY=VALUE\n", "seal_vm": true}'

# To enable SSH access, include ENABLE_SSH=true and optionally UNSEAL_PASSWORD in private_env,
# and add public keys to the bundle as /authorized_keys.

# Status includes host-side serial and QEMU log tails.
curl http://agent:8000/status/{deployment_id}

# Seal VM access (disable SSH and serial getty) by setting:
#   SEAL_VM=true
# When sealed, status omits host log tails after completion.

# Check status
curl http://agent:8000/status/{deployment_id}

# Health check
curl http://agent:8000/health
```

## TDX Host Requirements

### Prerequisites

- Intel TDX-capable CPU and BIOS configuration
- Ubuntu 24.04+ with TDX kernel
- libvirt + QEMU with TDX support
- QGS (Quote Generation Service) running

### QGS Setup

QGS listens on vsock (CID 2, port 4050):

```bash
systemctl status qgsd
sudo lsof -p $(pgrep qgs) | grep vsock
```

### AppArmor Configuration

Add vsock network permission for libvirt:

```bash
echo '  network vsock stream,' | sudo tee -a /etc/apparmor.d/abstractions/libvirt-qemu
sudo systemctl reload apparmor
```

### Device Permissions

```bash
sudo chmod 666 /dev/vhost-vsock /dev/vsock
```

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
