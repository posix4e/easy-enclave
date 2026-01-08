# Workflows

Legacy standalone workflows were removed in favor of the integrated pipelines
below.

## pipeline-dev.yml

Integrated dev pipeline (runs on `main` + manual):
- lint + SDK tests
- bake agent image + allowlist (tagged `dev`)
- deploy control plane
- deploy contacts example

Control plane DNS auto-update (Cloudflare):
- requires secrets `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ZONE` (or `CLOUDFLARE_ZONE_ID`)
- uses `EE_DNS_*` flags passed in `private-env` on deploy

## pipeline-release.yml

Integrated release pipeline (runs on `v*` tags):
- bake agent image + allowlist (tagged with the release)
- deploy control plane
- deploy contacts example
