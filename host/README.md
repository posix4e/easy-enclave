# Easy Enclave Host Tooling

Host-side VM tooling and installation for the Easy Enclave agent.

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
sudo ./host/install.sh
sudo systemctl status ee-agent
```

## Agent VM

To run the agent inside a dedicated VM, use `host/vm.py --agent` on a TDX host.
This bootstraps the agent via cloud-init and starts the service in the VM.

The agent VM waits for a deploy request and then starts the workload using
`docker compose` inside the VM.

By default the agent VM runs sealed (`SEAL_VM=true`).

To boot from a pre-baked pristine image:

```bash
sudo python3 host/vm.py --agent --agent-image /var/lib/easy-enclave/agent-pristine-v0.1.0.qcow2
```

## Pristine Agent Image

For releases, you can bake a pristine agent image using Canonical's TDX tooling.
This clones `canonical/tdx`, builds a TD guest image, boots once to install the
agent via cloud-init, then powers off and exports a clean qcow2.

```bash
sudo python3 host/vm.py \
  --build-pristine-agent-image \
  --vm-image-tag v0.1.0 \
  --tdx-guest-version 24.04 \
  --output-image /var/lib/easy-enclave/agent-pristine-v0.1.0.qcow2
```

The repo clone is stored in `/var/lib/easy-enclave/tdx` by default.
If `--vm-image-sha256` is omitted, the base image sha256 is computed automatically.

## Allowlist Generation

Generate a release allowlist on the TDX test node:

```bash
python3 host/scripts/generate_allowlist.py --release-tag v0.1.0
```

Upload `agent-attestation-allowlist.json` to the matching GitHub release.
