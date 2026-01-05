# Example Deployment

This example shows a multi-service docker-compose with public and private env injection.

## Files

- `docker-compose.yml`: sample workload with a public echo service and a whoami service.
- `public/banner.txt`: public bundle file included via the deploy workflow.

## Usage

The deploy workflow (`.github/workflows/deploy.yml`) uploads:
- `docker-compose.yml` as part of the public bundle
- `public/banner.txt` via `public-files`
- public env via `public-env`
- private env via `private-env`

Private env is merged into `/opt/workload/.env` inside the VM and is not persisted on the host.
