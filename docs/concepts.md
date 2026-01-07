---
layout: default
title: concepts
---

# concepts

The trust model explained.

## the problem

How do you know the server is running the code you expect?

- TLS certs prove domain ownership, not code
- Container signatures prove image integrity, not deployment
- "Trust the cloud"? That's still trusting someone

## the solution

Hardware attestation + GitHub as trust anchor.

## what is TDX

Intel Trust Domain Extensions. Creates isolated VMs with:

- **memory encryption** - hypervisor can't read TD memory
- **hardware attestation** - CPU-signed proof of what's running
- **measurement registers** - hash chain of boot components

## the TDX quote

When a TD requests attestation, the CPU generates a quote:

```
┌─────────────────────────────────────┐
│           TDX QUOTE                 │
├─────────────────────────────────────┤
│ RTMR0: firmware measurement         │
│ RTMR1: kernel measurement           │
│ RTMR2: application measurement      │
│ RTMR3: runtime data                 │
├─────────────────────────────────────┤
│ Report Data: custom payload         │
├─────────────────────────────────────┤
│ Signature: Intel CPU attestation    │
└─────────────────────────────────────┘
```

Signed by the CPU itself. Verifiable via Intel DCAP.

## github as trust anchor

```
Your Repo                    TDX Host
    │                            │
    │ docker-compose ───────────>│
    │                            │ deploy
    │                            │ generate quote
    │                            │
    │<────── attestation.json ───│
    │  (published as release)    │
    │                            │
    ▼                            │
 Client SDK                      │
    │ fetch release              │
    │ verify quote               │
    │ connect ──────────────────>│
```

Why GitHub?

- Your repo IS your identity
- Releases are immutable, auditable
- No PKI to manage
- Anyone can verify

## what you trust

- Intel TDX hardware
- GitHub releases
- Intel DCAP verification

## what you don't trust

- Cloud provider (memory encrypted)
- Host OS (TD is isolated)
- Network (cryptographic verification)
- Us (verification is end-to-end)

## sealed vs unsealed

**Sealed (production)**
- SSH disabled
- Serial disabled
- No way in except your app
- Attestation marked "sealed: true"

**Unsealed (development)**
- SSH available for debugging
- Attestation marked "sealed: false"
- Clients can reject unsealed services

## verification flow

When you call `connect("owner/repo")`:

1. Fetch latest release from GitHub
2. Extract TDX quote
3. Verify signature via Intel DCAP
4. Validate RTMR measurements
5. Check sealed flag
6. Return verified client

```python
from easyenclave import connect, VerificationError

try:
    client = connect("owner/repo")
except VerificationError as e:
    print(f"attestation failed: {e}")
```

## next

- [quickstart](/quickstart) - try it
- [sdk](/sdk) - API reference
- [action](/action) - deployment config
