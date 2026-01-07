# Easy Enclave Handoff (TDX Host Target)

## What changed recently
- Added integrated CI/CD workflows: `.github/workflows/pipeline-dev.yml` and `.github/workflows/pipeline-release.yml`.
- Removed legacy workflows: deploy-agent, deploy-contacts, deploy-control-plane, release-agent, release-agent-dev, and CI.
- Control plane runs as an **agent-managed workload** via `control_plane/docker-compose.yml` with Caddy in the same VM.
- Staging removed; control plane only allows sealed agents (`forge-1`).
- SDK `get_latest_attestation` now tolerates single-release payloads (tests pass).

## Current lifecycle
Dev pipeline (on `main` push and manual):
1) Lint + SDK tests
2) Bake dev agent image + allowlist (tag `dev`)
3) Deploy control plane
4) Deploy contacts example

Release pipeline (on `v*` tag):
1) Bake release agent image + allowlist (tag = release)
2) Deploy control plane
3) Deploy contacts example

## Key files
- `agent/agent.py`: single-VM agent (runs docker-compose in same VM) + attestation + tunnel client.
- `installer/host.py`: builds/bakes agent images and boots agent VM.
- `control_plane/docker-compose.yml`: control plane + Caddy stack.
- `control_plane/Caddyfile`: routes `*.app.easyenclave.com` to proxy.
- `sdk/easyenclave/github.py`: attestation fetching fix.

## Workflows to watch
- `.github/workflows/pipeline-dev.yml`
- `.github/workflows/pipeline-release.yml`

## Secrets required (GitHub)
- `AGENT_URL` (agent VM endpoint)
- `AGENT_SSH_HOST`, `AGENT_SSH_USER`, `AGENT_SSH_PORT`, `AGENT_SSH_KEY`
- `AGENT_VM_NAME`, `AGENT_VM_PORT`
- `CONTROL_GITHUB_TOKEN` (optional), `CONTROL_ADMIN_TOKEN` (optional)
- `DEMO_CONTACT_TOKEN`, `DEMO_UNSEAL_PASSWORD`

## TDX host notes
- Agent VM name: usually `ee-attestor`
- Agent VM IP (example): `192.168.122.38`
- Host repo: `/opt/easy-enclave` (ensure it’s up to date for baking)

## Open questions / next steps
- Confirm `pipeline-dev.yml` run success after latest push to `main`.
- If agent measurement mismatches, rebuild dev agent release and redeploy (pipeline should do this).
- Consider pruning old agent VMs/images on the host if storage grows.
