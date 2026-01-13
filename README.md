# EasyEnclave

[![Dev Pipeline](https://github.com/posix4e/easy-enclave/actions/workflows/pipeline-dev.yml/badge.svg)](https://github.com/posix4e/easy-enclave/actions/workflows/pipeline-dev.yml)

**The 1-click way to deploy attested software without changing your code.**

EasyEnclave is a hardware-attested confidential computing platform. Your code runs on Intel TDX, attestations are published to GitHub, and clients verify cryptographically. No certificates, no PKI, and no trust assumptions required.

[Read the whitepaper](https://easyenclave.com/whitepaper) to understand the economics and trust model.

## Core Concept

**GitHub Repo = Service Identity**: The repo IS the service. Clients connect to a repo, fetch attestations to learn:
1. What measurements to expect (TDX quote in attestation)
2. Where the service endpoint is (URL in attestation metadata)
3. Whether the VM was sealed (sealed flag in attestation)

**Model**: 1 TDX host = 1 GitHub repo = 1 attested service

This repository is the single source for the unified agent/control-plane daemon, installer, and Python SDK. The installer clones this repo on the host, so a given commit ties the deployed daemon and SDK verification logic together.

Single-VM design (current):
- The unified daemon runs inside the TD VM and can enable control-plane endpoints (`EE_CONTROL_PLANE_ENABLED=true`).
- The agent API deploys workloads via `docker compose` in that same VM.
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

Agents run in two modes: **sealed** (default, RA-TLS on, auto-connects to the production control plane at `wss://control.easyenclave.com/v1/tunnel`) and **unsealed** (`EE_MODE=unsealed`, enables the local control plane by default with RA-TLS off).

## Quick Start

### 1. Set Up TDX Agent

Prerequisites (host):
- BIOS configured for TDX (enable VMX/VT-d and TDX/TME per vendor guidance)
- TDX kernel + libvirt/QEMU with TDX support
- QGS running and enrolled with PCCS or a cloud collateral service

See `installer/README.md` for host setup details. From the repo root:

```bash
sudo ./installer/install.sh --non-interactive
```

Multiple agents per host are supported by running multiple agent VMs with unique
names and host ports. See `installer/README.md` for examples.

### 2. Configure Repository

Add the agent URL as a repository secret:

1. Go to **Settings → Secrets and variables → Actions**
2. Add secret: `AGENT_URL` = `https://your-tdx-host` (nginx on 443 routes to the RA-TLS listener)
3. (Optional) Admin endpoints are on `https://admin-<host>` and honor `AGENT_ADMIN_TOKEN`.

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
     +-------------------------+         create release
     | Agent VM (control)      |----------------------+
     | nginx 443 -> RA-TLS     |
     | control-plane enabled   |
     +-----------+-------------+
                 |
                 | EE_CONTROL_WS (tunnel)
                 v
     +-------------------------+
     | Agent VM (apps)         |
     | nginx 443 -> RA-TLS     |
     | runs workloads          |
     +-------------------------+
```

Releases:
- `pipeline-dev` resets agents via the admin API and redeploys the contacts app (no SSH).
- `pipeline-release` runs on `v*` tags, uses the tag as `agent-release-tag`, and deploys sealed by default.
- Each deployment publishes a `deploy-YYYYMMDD-HHMMSS` release with `attestation.json`
  (quote, endpoint, timestamp, sealed state).

CI/CD lifecycle:
- Release the agent VM image and publish the allowlist asset (`agent-attestation-allowlist.json`).
- For development, `.github/workflows/pipeline-dev.yml` calls `/admin/undeploy`, then deploys contacts using the composite action.
- The release pipeline (`.github/workflows/pipeline-release.yml`) mirrors the dev flow for tags and enforces RA-TLS client certs.
- Clients verify via the SDK using the latest deployment release attestation.

Notes:
- A single agent VM can only run one workload bundle at a time. For a persistent
  control plane, enable the control-plane endpoints on a dedicated agent VM and
  point other workloads at a different agent URL.
- App deploy workflows should pin `agent-release-tag` to a specific agent allowlist release.
- See `docs/workflows.md` for the three workflows (installer/reset, dev, release) and `action/` usage.

## License

MIT
