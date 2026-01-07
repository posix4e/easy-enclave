# Example Deployment

This example shows a multi-service docker-compose with public and private env injection.

## Contact Discovery (attested HMAC lookup)

`contact-compose.yml` builds a minimal contact discovery service that stores HMACs inside the
enclave. Clients submit contacts, and the service only sees HMACs derived from a sealed key.

Run locally:

```bash
CONTACT_API_TOKEN=local-token docker compose -f example/contact-compose.yml up --build
```

Register contacts:

```bash
curl -X POST http://localhost:8080/register \
  -H "Authorization: Bearer local-token" \
  -H "Content-Type: application/json" \
  -d '{"contacts": ["+15551234567", "+15559876543"]}'
```

Lookup:

```bash
curl -X POST http://localhost:8080/lookup \
  -H "Authorization: Bearer local-token" \
  -H "Content-Type: application/json" \
  -d '{"contacts": ["+15551234567", "+15551112222"]}'
```

## Files

- `docker-compose.yml`: sample workload with a public echo service and a whoami service.
- `public/banner.txt`: public bundle file included via the deploy workflow.

## Usage

The example deploy workflow lives inside `.github/workflows/pipeline-dev.yml` and uploads:
- `docker-compose.yml` as part of the public bundle
- `public/banner.txt` via `public-files`
- public env via `public-env`
- private env via `private-env`

The workflow expects an agent allowlist asset on the matching release tag. Set
`agent-release-tag` to the tag that contains `agent-attestation-allowlist.json`.

`example/verify.py` skips allowlist-only releases and uses the newest deployment
release that includes `attestation.json`.

Private env is merged into `/opt/workload/.env` inside the VM and is not persisted on the host.
