# Easy Enclave Action

Deploy workloads to a remote Easy Enclave agent and publish an attested release.

## Inputs

- `agent-url`: URL of the deployment agent
- `agent-release-tag`: release tag for agent allowlist
- `agent-allowlist-asset`: allowlist asset name (default: agent-attestation-allowlist.json)
- `agent-attestation-skip-pccs`: skip PCCS verification (default: false)
- `agent-skip-attestation`: skip agent attestation checks (default: false)
- `agent-ratls`: verify the agent via RA-TLS (auto|true|false)
- `agent-attestation-via-ssh`: fetch attestation via SSH to the host (default: false)
- `agent-ssh-host`: SSH host for agent VM provisioning
- `agent-ssh-user`: SSH user (default: ubuntu)
- `agent-ssh-key`: SSH private key for agent VM provisioning
- `agent-ssh-port`: SSH port (default: 22)
- `agent-vm-ref`: git ref to use for provisioning (default: main)
- `agent-vm-name`: agent VM name (default: ee-attestor)
- `agent-vm-port`: agent VM port (default: 8000)
- `agent-vm-image-tag`: agent VM image tag
- `agent-vm-image-sha256`: agent VM image sha256
- `docker-compose`: path to docker-compose.yml (bundled as a public artifact)
- `endpoint`: public endpoint URL (use `auto` for `http://{vm_ip}:8080`)
- `endpoint-port`: port for auto endpoint
- `github-token`: GitHub token used by the agent for release creation
- `vm-name`: VM name for local-mode runs (ignored when deploying via agent)
- `enable-ssh`: enable SSH access (default: false)
- `github-developer`: GitHub username to fetch public SSH keys from
- `unseal-password`: password for `ubuntu` when SSH is enabled
- `public-env`: newline-separated public env vars (bundled)
- `private-env`: newline-separated private env vars (sent inline)
- `public-files`: file or directory paths to bundle (comma or newline separated)
- `bundle-inline`: send the bundle inline instead of uploading an artifact (default: false)
- `cleanup-prefixes`: ignored in single-VM agent deployments
- `seal-vm`: seal VM access after deployment (default: true unless SSH enabled)

## Outputs

- `vm_ip`: IP address of the TD VM
- `quote`: base64-encoded TDX quote
- `release_url`: GitHub release URL

## Notes

- The action uploads a public bundle artifact (docker-compose + public files/env) unless `bundle-inline` is enabled.
- Private env values are sent inline to the agent and never stored on disk.
- When SSH access is requested, `seal-vm` is forced off for that deployment.
- Status polling prints host QEMU and serial log tails for visibility.
- Agent attestation is verified before and after deploy using the allowlist asset.
- When `agent-ratls` is enabled, the action verifies the agent RA-TLS cert using `quote_measurements` in the allowlist.
- Use an `https://` agent URL when RA-TLS is enabled.
- RA-TLS mode pins the agent TLS public key after verification and skips hostname checks.
- `agent-release-tag` must point to a release that includes the allowlist asset.
- For agent VM provisioning, use `.github/workflows/deploy-agent.yml`.
- The agent VM starts without a workload and waits for deploy requests.
- Each deployment replaces the previous workload on that agent VM. Use a dedicated
  agent VM for the control plane if you want it to stay running while deploying
  other apps.
- If the compose file lives in a subdirectory, that directory is bundled to keep
  build context paths intact. For root-level compose files, add build context files
  via `public-files`.
