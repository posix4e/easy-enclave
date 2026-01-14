---
layout: default
title: action
---

# github action

Deploy to TDX with attestation.

## basic usage

`.github/workflows/deploy.yml`:

```yaml
name: Deploy to TDX

on:
  workflow_dispatch:
    inputs:
      ssh:
        description: 'Enable SSH'
        type: boolean
        default: false

jobs:
  deploy:
    uses: anthropics/easyenclave/.github/workflows/pipeline-dev.yml@main
    with:
      ssh: ${{ inputs.ssh }}
    secrets: inherit
```

## pipelines

**pipeline-dev.yml** - development
- allows unsealed VMs
- sandbox network
- SSH optional

**pipeline-release.yml** - production
- sealed only
- production network
- no SSH

```yaml
jobs:
  deploy-prod:
    uses: anthropics/easyenclave/.github/workflows/pipeline-release.yml@main
    secrets: inherit
```

## inputs

| input | type | default | description |
|-------|------|---------|-------------|
| ssh | boolean | false | enable SSH access |
| compose_file | string | docker-compose.yml | compose path |
| public_dir | string | public/ | public files dir |

## environment variables

**public** - in docker-compose, bundled with artifact:

```yaml
services:
  app:
    environment:
      - LOG_LEVEL=info
      - PUBLIC_URL=https://api.example.com
```

**private** - from github secrets, never persisted:

```yaml
services:
  app:
    environment:
      - DATABASE_URL=${DATABASE_URL}
      - API_KEY=${API_KEY}
```

Add secrets: `Settings > Secrets > Actions`

Private vars are:
- transmitted inline, not in bundle
- injected directly into container
- never written to disk
- not in attestation artifacts

## required files

```
your-repo/
├── docker-compose.yml    # required
├── public/               # optional
│   └── config.json
└── .github/workflows/
    └── deploy.yml
```

## docker-compose

```yaml
version: '3.8'

services:
  app:
    image: ghcr.io/your-org/your-app:latest
    ports:
      - "8081:8080"
    environment:
      - DATABASE_URL=${DATABASE_URL}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8081/health"]
      interval: 30s
      timeout: 10s
      retries: 3
```

## outputs

GitHub release with `attestation.json`:

```json
{
  "endpoint": "https://your-app.easyenclave.com",
  "quote": "base64-tdx-quote...",
  "sealed": true,
  "rtmrs": {
    "rtmr0": "abc123...",
    "rtmr1": "def456...",
    "rtmr2": "ghi789...",
    "rtmr3": "jkl012..."
  },
  "timestamp": "2024-01-15T10:30:00Z"
}
```

## triggering

**UI:** Actions > Deploy to TDX > Run workflow

**CLI:**
```bash
# dev with SSH
gh workflow run deploy.yml -f ssh=true

# prod sealed
gh workflow run deploy.yml
```

## monitoring

```bash
gh run watch
gh run list --workflow=deploy.yml
gh run view --log
```

## troubleshooting

**stuck deployment**
- check Actions logs
- verify docker-compose
- ensure images accessible

**attestation failed**
- check agent logs in workflow
- verify host DCAP config

**container won't start**
- enable SSH: `ssh=true`
- check container logs
- verify env vars

## next

- [sdk](/sdk) - connect to your service
- [concepts](/concepts) - trust model
