---
layout: default
title: sdk
---

# python sdk

Connect to attested services.

## install

```bash
pip install easyenclave
```

## basic usage

```python
from easyenclave import connect

client = connect("owner/repo")
response = client.get("/api/data")
```

## connect()

```python
connect(
    repo,                      # "owner/repo"
    release="latest",          # release tag or "latest"
    require_sealed=True,       # reject unsealed VMs
    timeout=30,                # request timeout
) -> VerifiedClient
```

**raises:**
- `VerificationError` - quote verification failed
- `AttestationNotFound` - no attestation in release
- `UnsealedError` - VM unsealed + require_sealed=True
- `QuoteError` - TDX quote invalid

## VerifiedClient

Standard HTTP methods:

```python
client.get("/path", params={"key": "val"})
client.post("/path", json={"data": "val"})
client.put("/path", data="raw body")
client.delete("/path")
client.get("/path", headers={"X-Custom": "val"})
```

Properties:

```python
client.endpoint      # verified URL
client.attestation   # full attestation object
client.rtmrs         # RTMR measurements
client.sealed        # True if sealed
```

## error handling

```python
from easyenclave import (
    connect,
    VerificationError,
    AttestationNotFound,
    UnsealedError,
    QuoteError,
)

try:
    client = connect("owner/repo")
except AttestationNotFound:
    print("no attestation in release")
except UnsealedError:
    print("VM not sealed")
except QuoteError as e:
    print(f"quote invalid: {e}")
except VerificationError as e:
    print(f"verification failed: {e}")
```

## development mode

```python
# allow unsealed VMs (dev only!)
client = connect("owner/repo", require_sealed=False)

if not client.sealed:
    print("WARNING: unsealed VM")
```

> never use `require_sealed=False` in production

## attestation details

```python
client = connect("owner/repo")

att = client.attestation
print(f"endpoint: {att.endpoint}")
print(f"sealed: {att.sealed}")
print(f"quote: {att.quote[:50]}...")

for name, val in client.rtmrs.items():
    print(f"{name}: {val}")
```

## custom verification

```python
from easyenclave.verify import verify_quote, fetch_attestation

att = fetch_attestation("owner/repo", release="v1.0.0")
result = verify_quote(att.quote)

if result.valid:
    print(f"verified, tcb: {result.tcb_status}")
else:
    print(f"failed: {result.error}")
```

## example

```python
from easyenclave import connect

def main():
    client = connect("myorg/secure-api")

    health = client.get("/health").json()
    print(f"status: {health['status']}")

    response = client.post(
        "/api/process",
        json={"input": "sensitive"},
        headers={"Authorization": "Bearer token"}
    )
    print(response.json())

if __name__ == "__main__":
    main()
```

## next

- [concepts](/concepts) - trust model
- [action](/action) - deployment
