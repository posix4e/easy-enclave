# Workflows

## deploy.yml

Example deployment workflow that:
- builds a public bundle artifact (docker-compose + public env/files)
- sends private env inline to the agent
- polls deploy status and prints host log tails
- verifies the release attestation with the SDK

Inputs in the workflow are meant as a reference for `public-env`, `private-env`,
`public-files`, `github-developer`, and `unseal-password`.
