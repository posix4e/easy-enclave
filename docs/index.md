---
layout: default
title: home
---

# EASYENCLAVE

```
 ▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄
 █ DEPLOY TO TDX. ATTEST WITH GITHUB.          █
 █ TRUST NO ONE ELSE.                          █
 ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀
```

<p class="prompt">pip install easyenclave</p>

## what is this

Hardware-attested confidential computing. Your code runs on Intel TDX. Attestation published to GitHub. Clients verify cryptographically.

No certificates. No PKI. No trust assumptions.

## quick example

```python
from easyenclave import connect

# connects + verifies TDX attestation automatically
client = connect("your-org/your-repo")
response = client.get("/api/data")
```

## features

- **hardware attestation** - Intel TDX proves your code runs unmodified
- **github = identity** - your repo is your service identity
- **sealed VMs** - no SSH, no backdoors, no trust required
- **zero PKI** - attestations on github releases, not certificates
- **simple SDK** - one line to connect with verification

## get started

1. [quickstart](/quickstart) - deploy in 5 minutes
2. [concepts](/concepts) - understand the trust model
3. [sdk](/sdk) - python client reference
4. [action](/action) - github action config
