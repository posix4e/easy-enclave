# Easy Enclave Agent

HTTP service that deploys TD VMs and publishes attested releases.

## Install

```bash
sudo ./agent/install.sh
sudo systemctl status ee-agent
```

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
