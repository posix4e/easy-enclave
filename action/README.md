# Easy Enclave Action

Deploy workloads to a remote Easy Enclave agent and publish an attested release.

## Inputs

- `agent-url`: URL of the deployment agent
- `docker-compose`: path to docker-compose.yml (bundled as a public artifact)
- `endpoint`: public endpoint URL (use `auto` for `http://{vm_ip}:8080`)
- `endpoint-port`: port for auto endpoint
- `github-token`: GitHub token used by the agent for release creation
- `vm-name`: VM name on the host
- `enable-ssh`: enable SSH access (default: false)
- `github-developer`: GitHub username to fetch public SSH keys from
- `unseal-password`: password for `ubuntu` when SSH is enabled
- `public-env`: newline-separated public env vars (bundled)
- `private-env`: newline-separated private env vars (sent inline)
- `public-files`: file paths to bundle (comma or newline separated)
- `cleanup-prefixes`: VM name prefixes to cleanup before deploy
- `seal-vm`: seal VM access after deployment (default: true unless SSH enabled)

## Outputs

- `vm_ip`: IP address of the TD VM
- `quote`: base64-encoded TDX quote
- `release_url`: GitHub release URL

## Notes

- The action uploads a public bundle artifact (docker-compose + public files/env).
- Private env values are sent inline to the agent and never stored on disk.
- When SSH access is requested, `seal-vm` is forced off for that deployment.
- Status polling prints host QEMU and serial log tails for visibility.
