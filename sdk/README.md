# Easy Enclave Python SDK

Python client library for Easy Enclave - TDX attestation via GitHub.

## Installation

```bash
pip install easyenclave
```

## Usage

```python
from easyenclave import connect

# Connect to an attested service
client = connect("owner/repo")

# Access the verified endpoint
print(f"Endpoint: {client.endpoint}")
print(f"Measurements: {client.measurements}")
```

## Features

- Fetch attestations from GitHub releases
- Verify TDX quotes via DCAP
- Extract and validate RTMR measurements

## License

MIT
