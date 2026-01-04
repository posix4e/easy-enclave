# Easy Enclave

A TDX attestation platform using GitHub as the trust anchor. Deploy workloads to TDX hosts with remote attestation stored as GitHub release attestations.

## Core Concept

**GitHub Repo = Service Identity**: The repo IS the service. Clients connect to a repo, fetch attestations to learn:
1. What measurements to expect (TDX quote in attestation)
2. Where the service endpoint is (URL in attestation metadata)

**Model**: 1 TDX host = 1 GitHub repo = 1 attested service

## How It Works

```
┌─────────────────────────────────────────────────────────────────┐
│  TDX Host                                                       │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  GitHub Runner + Workload                                 │  │
│  │  - Runs docker-compose                                    │  │
│  │  - Generates TDX quote                                    │  │
│  │  - Publishes attestation to GitHub                        │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
         │                                      │
         │ Attestation                          │ Service
         ▼                                      ▼
┌─────────────────┐                    ┌─────────────────┐
│  GitHub Release │◄───────────────────│  Client         │
│  - TDX Quote    │   fetch + verify   │  easyenclave    │
│  - Endpoint URL │                    │  .connect()     │
└─────────────────┘                    └─────────────────┘
```

## Components

- **GitHub Action** (`easy-enclave/attest`) - Generates TDX quotes, creates attested releases, runs workloads
- **Python SDK** (`easyenclave`) - Client library: `connect("owner/repo")` with DCAP verification

## Usage

### Deploy (GitHub Action)
```yaml
jobs:
  deploy:
    runs-on: [self-hosted, tdx]
    steps:
      - uses: easy-enclave/attest@v1
        with:
          docker-compose: ./docker-compose.yml
          endpoint: "https://myservice.example.com:8443"
```

### Connect (Python SDK)
```python
from easyenclave import connect

# Fetches attestation from GitHub, verifies TDX quote via Intel PCCS
client = connect("acme/my-service")
```

## Roadmap

### CRAWL (Current)
Minimal end-to-end demo. Workloads run directly on TDX host.

- [ ] **GitHub Action**: Quote generation, release creation, docker-compose
- [ ] **Python SDK**: Fetch, verify, `connect()`
- [ ] **Demo**: Simple service + verification script

### WALK
Production isolation with TD VMs.

- [ ] libvirt integration - workloads in isolated TD VMs
- [ ] Quote from workload VM (not host)
- [ ] VM lifecycle management

### RUN
Repeatable host provisioning.

- [ ] Ansible/cloud-init for TDX host setup
- [ ] One-command provisioning

## V2 (Future)

- Browser extension for visual attestation
- Dashboard for repo/host registration
- **Credits system**: Earn credits by providing TDX hosts, spend credits to deploy workloads
- Discovery service + multi-host

## TDX Host Setup

Required configurations for the TDX host:

### QGS (Quote Generation Service)
QGS listens on vsock (CID 2, port 4050). Verify it's running:
```bash
systemctl status qgsd
sudo lsof -p $(pgrep qgs) | grep vsock
```

### AppArmor
Add vsock network permission for libvirt:
```bash
echo '  network vsock stream,' | sudo tee -a /etc/apparmor.d/abstractions/libvirt-qemu
sudo systemctl reload apparmor
```

### QEMU Sandbox (optional)
If vsock still fails, disable QEMU seccomp sandbox:
```bash
sudo sed -i 's/#seccomp_sandbox = 1/seccomp_sandbox = 0/' /etc/libvirt/qemu.conf
sudo systemctl restart libvirtd
```

### Device Permissions
```bash
sudo chmod 666 /dev/vhost-vsock /dev/vsock
```

## License

MIT
