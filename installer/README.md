# Easy Enclave Host Tooling

Host-side VM tooling and installation for the Easy Enclave agent.

## Host Setup

### Prerequisites

- Intel TDX-capable CPU and BIOS configured for TDX (enable VMX/VT-d and TDX/TME per vendor guidance)
- Ubuntu 24.04+ with TDX kernel
- libvirt + QEMU with TDX support
- QGS (Quote Generation Service) running and enrolled with PCCS or a cloud collateral service

### QGS Setup

QGS listens on vsock (CID 2, port 4050):

```bash
systemctl status qgsd
sudo lsof -p $(pgrep qgs) | grep vsock
```

Confirm the collateral service endpoint (PCCS or cloud equivalent) is configured:

```bash
grep -E '^(PCCS_URL|COLLATERAL_SERVICE_URL)=' /etc/sgx_default_qcnl.conf
```

The installer fails fast if QGS is inactive or no collateral service is configured.

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

From the repo root, run the turnkey installer:

```bash
sudo ./installer/install.sh --non-interactive
sudo systemctl status ee-agent
```

`installer/install.sh` accepts flags for non-interactive installs and VM mode:

```bash
sudo ./installer/install.sh --mode vm --non-interactive --vm-name ee-agent
```

The agent starts in **sealed** mode by default (RA-TLS on, auto-connects to the production control plane). To run self-contained for local testing, set `EE_MODE=unsealed` in `/etc/systemd/system/ee-agent.service` so it hosts its own control plane with RA-TLS off.

## Agent VM

To run the agent inside a dedicated VM, use `installer/host.py --agent` on a TDX host.
This bootstraps the agent via cloud-init and starts the service inside the TD VM.

The agent VM waits for a deploy request and then starts `docker compose` inside
the same VM.

By default the agent VM runs sealed (`SEAL_VM=true`).

To boot from a pre-baked pristine image:

```bash
sudo python3 installer/host.py --agent --agent-image /var/lib/easy-enclave/agent-pristine-v0.1.0.qcow2
```

### Multiple Agents Per Host

Run multiple agent VMs on the same host by giving each VM a unique name and
forwarding a unique host port to the VM's public nginx port (default 443):

```bash
sudo python3 installer/host.py --agent --name ee-attestor-control --port 8000 --public-port 443 --host-port 8443
sudo python3 installer/host.py --agent --name ee-attestor-apps --port 8000 --public-port 443 --host-port 9443
```

Use the host port in `AGENT_URL` (for example, `https://<host-ip>:9443`).

## Pristine Agent Image

For releases, you can bake a pristine agent image using Canonical's TDX tooling.
This clones `canonical/tdx`, builds a TD guest image, boots once to install the
agent via cloud-init, then powers off and exports a clean qcow2.

```bash
sudo python3 installer/host.py \
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
python3 installer/scripts/generate_allowlist.py --release-tag v0.1.0
```

If the agent is serving RA-TLS, use the HTTPS URL with `--insecure`:

```bash
python3 installer/scripts/generate_allowlist.py \
  --attestation-url https://agent:443/attestation \
  --insecure \
  --release-tag v0.1.0
```

Upload `agent-attestation-allowlist.json` to the matching GitHub release.
RA-TLS verification uses the `quote_measurements` field, so regenerate the allowlist
after upgrading agent builds.
