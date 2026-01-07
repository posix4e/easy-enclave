# Workflows

## deploy.yml

Example deployment workflow that:
- builds a public bundle artifact (docker-compose + public env/files)
- sends private env inline to the agent
- polls deploy status and prints host log tails
- verifies the release attestation with the SDK

Inputs in the workflow are meant as a reference for `public-env`, `private-env`,
`public-files`, `github-developer`, and `unseal-password`.

## deploy-agent.yml

Creates an agent VM on a bare-metal TDX host over SSH. Requires secrets:

- `AGENT_HOST`
- `AGENT_USER`
- `AGENT_SSH_KEY`

Inputs:
- `ref`: git ref for `installer/host.py`
- `vm_image_tag`: image tag for allowlist matching
- `vm_image_sha256`: image hash for allowlist matching
- `vm_name`: agent VM name
- `vm_port`: agent port

## deploy-control-plane.yml

Deploys the control plane + proxy onto a TDX host over SSH. Requires secrets:

- `CONTROL_HOST`
- `CONTROL_USER`
- `CONTROL_SSH_KEY`
- `CONTROL_GITHUB_TOKEN` (optional, for private allowlist assets)
- `CONTROL_ADMIN_TOKEN` (optional, protects `/v1/apps`)

Input:
- `target`: `prod` or `staging`
