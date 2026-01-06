# Easy Enclave Agent

HTTP service that deploys docker-compose workloads and publishes attested
releases. The agent runs inside the TD VM and launches workloads in that same VM.

Host setup, VM tooling, and allowlist generation live in `installer/README.md`.

## API

### POST /deploy

Starts a deployment using a public bundle artifact and private env.

Required:
- `repo`: GitHub repo in `owner/repo` format
- `bundle_artifact_id`: Actions artifact id for the bundle

Optional:
- `private_env`: newline-separated env vars (not stored on disk)
- `cleanup_prefixes`: list of VM name prefixes to clean before deploy
- `vm_name`: VM name override
- `port`: service port (default 8080)
- `seal_vm`: seal access after deploy

### GET /status/{id}

Returns deployment status and host log tails:
- `host_logs.qemu`: QEMU log tail
- `host_logs.serial`: serial console log tail

### GET /health

Simple health check.

## Bundle Contents

The bundle is an artifact zip that must include `docker-compose.yml` and may include:
- `.env.public` (public env)
- `authorized_keys` (SSH keys for `ubuntu`)
- any additional files needed by the workload

The agent combines `.env.public` and `private_env` into `/opt/workload/.env`.

## Security Notes

- Private env is never written to `/var/lib/easy-enclave/deployments`.
- Sealed deployments disable SSH and serial getty inside the VM.

## Agent Attestation

The agent can return a TDX quote and measurements:

```bash
curl http://agent:8000/attestation
```

The response includes:
- `quote`: base64 TDX quote
- `report_data`: hex report data bound to measurements
- `measurements`: hashes + `vm_image_id` + `sealed`

Set `/etc/easy-enclave/vm_image_id` with:

```
tag=<release-tag>
sha256=<image-sha256>
```

## Tunnel Client (Control Plane)

The agent runs the tunnel client inside the same process. Set the environment
variables below to enable the WebSocket tunnel and proxy forwarding.

Required:
- `EE_CONTROL_WS` (e.g. `ws://control-plane:8088/v1/tunnel`)
- `EE_REPO`
- `EE_RELEASE_TAG`
- `EE_APP_NAME`

Optional:
- `EE_NETWORK` (default `forge-1`)
- `EE_BACKEND_URL` (default `http://127.0.0.1:8080`)
- `EE_HEALTH_INTERVAL_SEC` (default `60`)
- `EE_RECONNECT_DELAY_SEC` (default `5`)
