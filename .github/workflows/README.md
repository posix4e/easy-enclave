# Workflows

Legacy standalone workflows were removed in favor of the integrated pipelines
below.

## pipeline-dev.yml

Integrated dev pipeline (runs on `main` + manual):
- lint + SDK tests
- bake agent image + allowlist (tagged `dev`)
- reset agents via admin vhost
- deploy contacts example

Control plane DNS auto-update (Cloudflare):
- requires secrets `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ZONE` (or `CLOUDFLARE_ZONE_ID`)
- uses `EE_DNS_*` flags in the agent VM environment

## pipeline-release.yml

Integrated release pipeline (runs on `v*` tags):
- bake agent image + allowlist (tagged with the release)
- reset agents via admin vhost
- deploy contacts example
