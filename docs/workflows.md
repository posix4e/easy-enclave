# Workflows and Actions

This repo ships three GitHub workflows and a reusable composite action under `action/` so anyone can deploy or reset agents without digging into host internals.

## Installer / Reset (SSH)
- File: `.github/workflows/reset-agent.yml`
- Purpose: full reset on a host via SSH (uninstall everything, reinstall control-plane, then install contacts).
- Inputs: `AGENT_SSH_HOST`, `AGENT_SSH_USER`, `AGENT_SSH_PORT`, `AGENT_SSH_KEY`, and optional `CONTROL_PUBLIC_IP` for port forwarding.
- DNS/ports: uses the installer to set up iptables forwarding; if `CLOUDFLARE_*` secrets are present, it also upserts `control.easyenclave.com` and `contacts.easyenclave.com` to the provided public IPs.
- When to use: new hosts, or when the agent API is unreachable.
- Defaults: agents listen on port 8000 inside the VM; host port 443 is forwarded to the agent. To serve TLS via Caddy inside the control-plane VM, forward host 80/443 to the VM’s 80/443 instead of 443→8000.

## Dev Pipeline (API-Only)
- File: `.github/workflows/pipeline-dev.yml`
- Purpose: undeploy both agents via `/admin/undeploy`, redeploy control-plane (unsealed) and the contacts app.
- Secrets: `AGENT_URL_CONTROL`, `AGENT_URL_CONTACTS` (or fallback `AGENT_URL`), `AGENT_ADMIN_TOKEN`, `CONTROL_GITHUB_TOKEN`, `CONTROL_ADMIN_TOKEN`, `DEMO_CONTACT_TOKEN`, `DEMO_UNSEAL_PASSWORD`, optional Cloudflare vars.
- What it does: POSTs to admin endpoints to get a clean state, then uses the `./action` composite to push bundles inline with RA-TLS enabled by default.
- Health: control-plane serves on 8088/9090 inside the VM; contacts sample runs on 8000. If fronted by Caddy/DNS, expect 80/443 on the host.

## Release Pipeline (API-Only, Sealed)
- File: `.github/workflows/pipeline-release.yml`
- Trigger: tags matching `v*` or manual dispatch.
- Behavior: same flow as dev but sealed VMs and `EE_RATLS_REQUIRE_CLIENT_CERT=true`. Uses the tag name as `agent-release-tag` for allowlist lookup.

## Composite Action (`action/`)
- Entry point: `./action/action.yml`
- Usage: deploy any compose bundle to an agent with inline or artifact bundles, optional RA-TLS verification, and GitHub release creation.
- Publish: keep `action/` in release artifacts so downstream users can reference it directly (`uses: ./action` in this repo or `owner/repo@ref` when packaged).

## Quick Start Flow
1. Run `reset-agent.yml` once to provision a host.
2. For daily work, trigger `pipeline-dev` (or `pipeline-release` on tags); both talk to the agent over HTTPS and avoid SSH.
3. Inspect attestation from the deployment release created by the composite action; clients validate via the SDK.
4. For local/self-contained testing, set `EE_MODE=unsealed` so the agent runs its own control plane (RA-TLS off by default) instead of dialing the production control plane.
