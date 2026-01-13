# Easy Enclave Agent

HTTP service that deploys docker-compose workloads and publishes attested
releases. The agent runs inside the TD VM and launches workloads in that same VM.

Host setup, VM tooling, and allowlist generation live in `installer/README.md`.

## Modes

- **Sealed (default)**: agent connects outbound to the production control plane (`wss://control.easyenclave.com/v1/tunnel`) with RA-TLS.
- **Unsealed** (`EE_MODE=unsealed`): agent disables the outbound tunnel and enables the local control-plane endpoints; RA-TLS is off by default in this mode.

## API

### POST /deploy

Starts a deployment using a public bundle artifact and private env.

Required:
- `repo`: GitHub repo in `owner/repo` format
- `bundle_artifact_id`: Actions artifact id for the bundle

Optional:
- `private_env`: newline-separated env vars (not stored on disk)
- `port`: service port (default 8080)
- `seal_vm`: seal access after deploy

Notes:
- `cleanup_prefixes` and `vm_name` are ignored in single-VM mode.

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
curl -k https://agent:443/attestation
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
- `EE_CONTROL_WS` (e.g. `wss://control.easyenclave.com/v1/tunnel`)
- `EE_REPO`
- `EE_RELEASE_TAG`
- `EE_APP_NAME`

Optional:
- `EE_NETWORK` (default `forge-1`)
- `EE_BACKEND_URL` (default `http://127.0.0.1:8080`)
- `EE_HEALTH_INTERVAL_SEC` (default `60`)
- `EE_RECONNECT_DELAY_SEC` (default `5`)

RA-TLS:
- `EE_RATLS_ENABLED` (default `true`)
- `EE_RATLS_CERT_TTL_SEC` (default `3600`)
- `EE_RATLS_SKIP_PCCS` (default `false`)
- `EE_RATLS_PCCS_URL` (optional override for PCCS)
- `EE_CONTROL_ALLOWLIST_PATH` (optional path to control-plane allowlist JSON)
- `EE_CONTROL_ALLOWLIST_REPO` / `EE_CONTROL_ALLOWLIST_TAG` (fetch allowlist from GitHub)
- `EE_CONTROL_ALLOWLIST_ASSET` (default `agent-attestation-allowlist.json`)
- `EE_CONTROL_ALLOWLIST_REQUIRED` (default `true`)
- `EE_CONTROL_ALLOWLIST_TOKEN` (optional token for private allowlist)
