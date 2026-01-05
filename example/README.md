# Example Deployment

This example shows a multi-service docker-compose with public and private env injection.

## Files

- `docker-compose.yml`: sample workload with a public echo service and a whoami service.
- `public/banner.txt`: public bundle file included via the deploy workflow.

## Usage

The example deploy workflow lives at `.github/workflows/deploy.yml` and uploads:
- `docker-compose.yml` as part of the public bundle
- `public/banner.txt` via `public-files`
- public env via `public-env`
- private env via `private-env`

The workflow expects an agent allowlist asset on the matching release tag. Set
`agent-release-tag` to the tag that contains `agent-attestation-allowlist.json`.

Private env is merged into `/opt/workload/.env` inside the VM and is not persisted on the host.
