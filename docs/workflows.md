# Workflows and Actions

This repo ships three GitHub workflows and a reusable composite action under `action/` so anyone can deploy or reset agents without digging into host internals.

## Installer / Reset (SSH)
- File: `.github/workflows/reset-agent.yml`
- Purpose: full reset on a host via SSH (uninstall everything, reinstall control-plane, then install contacts).
- Inputs: `AGENT_SSH_HOST`, `AGENT_SSH_USER`, `AGENT_SSH_PORT`, `AGENT_SSH_KEY`, optional `CONTROL_PUBLIC_IP` for port forwarding, and `vm_image_tag`/`force_rebuild` when you want a fresh pristine image.
- DNS/ports: uses the installer to set up iptables forwarding; if `CLOUDFLARE_*` secrets are present, it also upserts `control.easyenclave.com`, `admin-control.easyenclave.com`, `contacts.easyenclave.com`, and `admin-contacts.easyenclave.com` to the provided public IPs.
- When to use: new hosts, or when the agent API is unreachable.
- Defaults: agent main listens on 8000 inside the VM; nginx listens on 443 and routes by SNI. Host port 443 is forwarded to VM port 443. Admin traffic uses the `admin-<host>` vhost and is terminated by nginx (non RA-TLS).

## Dev Pipeline (API-Only)
- File: `.github/workflows/pipeline-dev.yml`
- Purpose: undeploy both agents via `/admin/undeploy`, then deploy the contacts app.
- Secrets: `AGENT_URL_CONTROL`, `AGENT_URL_CONTACTS` (or fallback `AGENT_URL`), `AGENT_ADMIN_TOKEN`, `DEMO_CONTACT_TOKEN`, `DEMO_UNSEAL_PASSWORD`.
- What it does: POSTs to admin vhosts to get a clean state, then uses the `./action` composite to push bundles inline with RA-TLS enabled by default.
- Health: main listener is 8000 inside the VM; admin HTTP is 8080; proxy is 9090. External traffic hits nginx on 443.

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
