# Easy Enclave Agent

HTTP service that deploys TD VMs and publishes attested releases.

## Host Setup

### Prerequisites

- Intel TDX-capable CPU and BIOS configuration
- Ubuntu 24.04+ with TDX kernel
- libvirt + QEMU with TDX support
- QGS (Quote Generation Service) running

### QGS Setup

QGS listens on vsock (CID 2, port 4050):

```bash
systemctl status qgsd
sudo lsof -p $(pgrep qgs) | grep vsock
```

### AppArmor Configuration

Add vsock network permission for libvirt:

```bash
echo '  network vsock stream,' | sudo tee -a /etc/apparmor.d/abstractions/libvirt-qemu
sudo systemctl reload apparmor
```

### Device Permissions

```bash
sudo chmod 666 /dev/vhost-vsock /dev/vsock
```

## Install

```bash
sudo ./agent/install.sh
sudo systemctl status ee-agent
```

## Agent VM

To run the agent inside a dedicated VM, use `action/src/vm.py --agent` on a TDX host.
This bootstraps the agent via cloud-init and starts the service in the VM.

The agent VM waits for a deploy request and then starts the workload using
`docker compose` inside the VM.

By default the agent VM runs sealed (`SEAL_VM=true`).

## Pristine Agent Image

For releases, you can bake a pristine agent image using Canonical's TDX tooling.
This clones `canonical/tdx`, builds a TD guest image, boots once to install the
agent via cloud-init, then powers off and exports a clean qcow2.

```bash
sudo python3 action/src/vm.py \
  --build-pristine-agent-image \
  --vm-image-tag v0.1.0 \
  --vm-image-sha256 <base-image-sha256> \
  --tdx-guest-version 24.04 \
  --output-image /var/lib/easy-enclave/agent-pristine-v0.1.0.qcow2
```

The repo clone is stored in `/var/lib/easy-enclave/tdx` by default.

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

### Allowlist Generation

Generate a release allowlist on the TDX test node:

```bash
python3 agent/scripts/generate_allowlist.py --release-tag v0.1.0
```

Upload `agent-attestation-allowlist.json` to the matching GitHub release.
