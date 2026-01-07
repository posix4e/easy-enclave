---
layout: default
title: examples
---

# examples

Real-world usage patterns.

## contact discovery service

Private contact matching inside an enclave. Clients submit phone numbers, service stores only HMACs derived from a sealed key.

```
┌──────────────┐     ┌─────────────────────────┐
│   Client     │     │   TDX Enclave           │
│              │     │                         │
│ +1555123456 ─┼────>│ HMAC(key, phone) ──────>│ store
│              │     │                         │
│ lookup? ─────┼────>│ compare HMACs ─────────>│ match
│              │     │                         │
│ <────────────┼─────│ [matched contacts]      │
└──────────────┘     └─────────────────────────┘
```

the key never leaves the enclave. even we can't see your contacts.

### run locally

```bash
CONTACT_API_TOKEN=local-token \
docker compose -f example/contact-compose.yml up --build
```

### register contacts

```bash
curl -X POST http://localhost:8080/register \
  -H "Authorization: Bearer local-token" \
  -H "Content-Type: application/json" \
  -d '{"contacts": ["+15551234567", "+15559876543"]}'
```

### lookup matches

```bash
curl -X POST http://localhost:8080/lookup \
  -H "Authorization: Bearer local-token" \
  -H "Content-Type: application/json" \
  -d '{"contacts": ["+15551234567", "+15551112222"]}'
```

response:
```json
{
  "matches": ["+15551234567"],
  "count": 1
}
```

### deploy to TDX

```yaml
name: Deploy Contact Service

on: workflow_dispatch

jobs:
  deploy:
    uses: anthropics/easyenclave/.github/workflows/pipeline-dev.yml@main
    with:
      compose_file: example/contact-compose.yml
    secrets: inherit
```

set `CONTACT_API_TOKEN` as a GitHub secret. it's injected at runtime, never persisted.

## echo service

basic multi-service example with public/private env:

```yaml
version: '3.8'

services:
  echo:
    image: hashicorp/http-echo
    args: ["-text=hello from TDX"]
    ports:
      - "8080:5678"

  whoami:
    image: traefik/whoami
    ports:
      - "8081:80"
```

## file structure

```
example/
├── docker-compose.yml      # echo + whoami
├── contact-compose.yml     # contact discovery
├── public/
│   └── banner.txt          # bundled public file
└── verify.py               # SDK verification example
```

## verification example

`example/verify.py` shows SDK usage:

```python
from easyenclave import connect

client = connect("posix4e/easy-enclave")

# check attestation details
print(f"sealed: {client.sealed}")
print(f"endpoint: {client.endpoint}")

for name, val in client.rtmrs.items():
    print(f"{name}: {val[:16]}...")

# make request
response = client.get("/health")
print(response.json())
```

## env injection

**public env** - bundled with artifact, visible in attestation:
```yaml
environment:
  - LOG_LEVEL=info
  - PUBLIC_URL=https://api.example.com
```

**private env** - injected at runtime, never written to disk:
```yaml
environment:
  - DATABASE_URL=${DATABASE_URL}    # github secret
  - API_KEY=${API_KEY}              # github secret
  - CONTACT_API_TOKEN=${CONTACT_API_TOKEN}
```

## next

- [control-plane](/control-plane) - routing service
- [sdk](/sdk) - python client
- [quickstart](/quickstart) - get started
