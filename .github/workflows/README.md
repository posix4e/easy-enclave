# Workflows

## deploy-contacts.yml

Contacts example deployment workflow that:
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

Deploys the control plane as an agent-managed workload (TD VM). Requires secrets:

- `AGENT_URL`
- `CONTROL_GITHUB_TOKEN` (optional, for private allowlist assets)
- `CONTROL_ADMIN_TOKEN` (optional, protects `/v1/apps`)

Inputs:
- `agent-release-tag`: allowlist release tag for agent attestation

## pipeline-dev.yml

Integrated dev pipeline (runs on `main` + manual):
- bake agent image + allowlist (tagged `dev`)
- deploy control plane
- deploy contacts example

## pipeline-release.yml

Integrated release pipeline (runs on `v*` tags):
- bake agent image + allowlist (tagged with the release)
- deploy control plane
- deploy contacts example

## release-agent-dev.yml

Builds a dev agent image + allowlist on every push to `main` (and on manual run),
publishing the allowlist to the `dev` release tag.
