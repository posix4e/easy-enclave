# Easy Enclave Handoff (TDX Host Target)

## What changed recently
- Added integrated CI/CD workflows: `.github/workflows/pipeline-dev.yml` and `.github/workflows/pipeline-release.yml`.
- Removed legacy workflows: deploy-agent, deploy-contacts, deploy-control-plane, release-agent, release-agent-dev, and CI.
- Control plane is embedded in the unified agent process (`EE_CONTROL_PLANE_ENABLED=true`); nginx in the VM handles TLS routing.
- Staging removed; control plane only allows sealed agents (`forge-1`).
- SDK `get_latest_attestation` now tolerates single-release payloads (tests pass).

## Current lifecycle
Dev pipeline (on `main` push and manual):
1) Lint + SDK tests
2) Bake dev agent image + allowlist (tag `dev`)
3) Undeploy agents via admin vhost
4) Deploy contacts example

Release pipeline (on `v*` tag):
1) Bake release agent image + allowlist (tag = release)
2) Undeploy agents via admin vhost
3) Deploy contacts example

## Key files
- `agent/agent.py`: single-VM agent (runs docker-compose in same VM) + attestation + tunnel client.
- `installer/host.py`: builds/bakes agent images and boots agent VM.
- `installer/templates/nginx.conf`: nginx SNI routing for RA-TLS vs admin vhost.
- `sdk/easyenclave/github.py`: attestation fetching fix.

## Workflows to watch
- `.github/workflows/pipeline-dev.yml`
- `.github/workflows/pipeline-release.yml`

## Secrets required (GitHub)
- `AGENT_URL` (agent VM endpoint)
- `AGENT_SSH_HOST`, `AGENT_SSH_USER`, `AGENT_SSH_PORT`, `AGENT_SSH_KEY`
- `AGENT_VM_NAME`, `AGENT_VM_PORT`
- `AGENT_ADMIN_TOKEN` (optional)
- `DEMO_CONTACT_TOKEN`, `DEMO_UNSEAL_PASSWORD`

## TDX host notes
- Agent VM name: usually `ee-attestor`
- Agent VM IP (example): `192.168.122.38`
- Host repo: `/opt/easy-enclave` (ensure it’s up to date for baking)

## Open questions / next steps
- Confirm `pipeline-dev.yml` run success after latest push to `main`.
- If agent measurement mismatches, rebuild dev agent release and redeploy (pipeline should do this).
- Consider pruning old agent VMs/images on the host if storage grows.
