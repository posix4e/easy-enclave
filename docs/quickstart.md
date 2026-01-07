---
layout: default
title: quickstart
---

# quickstart

Get attested in 5 minutes.

## prerequisites

- Python 3.9+
- A GitHub repo
- A docker-compose.yml

> Don't have TDX hardware? No problem. We handle deployment. You just connect.

## step 1: install

```bash
pip install easyenclave
```

## step 2: add the workflow

Create `.github/workflows/deploy.yml`:

```yaml
name: Deploy to TDX

on:
  workflow_dispatch:
    inputs:
      ssh:
        description: 'Enable SSH (dev only)'
        type: boolean
        default: false

jobs:
  deploy:
    uses: anthropics/easyenclave/.github/workflows/pipeline-dev.yml@main
    with:
      ssh: ${{ inputs.ssh }}
    secrets: inherit
```

## step 3: docker-compose

```yaml
version: '3.8'

services:
  app:
    image: your-image:latest
    ports:
      - "8080:8080"
    environment:
      - DATABASE_URL=${DATABASE_URL}  # from github secrets
```

## step 4: deploy

```bash
gh workflow run deploy.yml
```

What happens:
1. docker-compose bundled and sent to TDX host
2. Agent generates TDX quote (hardware attestation)
3. Attestation published as GitHub release
4. Service is live and verifiable

## step 5: connect

```python
from easyenclave import connect

client = connect("your-org/your-repo")
response = client.get("/health")
print(response.json())
```

Done. Attestation verified. Connection secure.

## next

- [concepts](/concepts) - how it works under the hood
- [sdk](/sdk) - full API reference
- [action](/action) - workflow configuration
