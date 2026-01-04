#!/usr/bin/env python3
"""
Create GitHub release with TDX attestation data.

Attaches TDX quote and endpoint information to a GitHub release.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone


def create_release(
    quote: str,
    endpoint: str,
    measurements: str,
    repo: str = None,
    token: str = None
) -> str:
    """
    Create a GitHub release with attestation data.

    Args:
        quote: Base64-encoded TDX quote
        endpoint: Service endpoint URL
        measurements: JSON string of measurements
        repo: Repository (owner/repo format)
        token: GitHub token

    Returns:
        Release URL
    """
    repo = repo or os.environ.get('GITHUB_REPOSITORY')
    token = token or os.environ.get('GITHUB_TOKEN')

    if not repo or not token:
        raise ValueError("GITHUB_REPOSITORY and GITHUB_TOKEN must be set")

    # Generate release tag based on timestamp
    now = datetime.now(timezone.utc)
    timestamp = now.strftime('%Y%m%d-%H%M%S')
    tag = f"deploy-{timestamp}"

    # Build attestation JSON
    attestation = {
        "version": "1.0",
        "quote": quote,
        "endpoint": endpoint,
        "measurements": json.loads(measurements) if isinstance(measurements, str) else measurements,
        "timestamp": now.isoformat().replace('+00:00', 'Z'),
        "repo": repo
    }

    # Release body with attestation
    body = f"""## TDX Attested Deployment

**Endpoint**: {endpoint}

**Timestamp**: {attestation['timestamp']}

### Attestation Data

```json
{json.dumps(attestation, indent=2)}
```

### Verification

```python
from easyenclave import connect

client = connect("{repo}")
# Verifies TDX quote and returns connection to endpoint
```
"""

    # Create release using gh CLI
    try:
        result = subprocess.run(
            [
                'gh', 'release', 'create', tag,
                '--repo', repo,
                '--title', f'Deployment {timestamp}',
                '--notes', body,
            ],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, 'GITHUB_TOKEN': token}
        )
        print(f"Created release: {tag}")

        # Also save attestation as a release asset
        attestation_file = '/tmp/attestation.json'
        with open(attestation_file, 'w') as f:
            json.dump(attestation, f, indent=2)

        subprocess.run(
            [
                'gh', 'release', 'upload', tag, attestation_file,
                '--repo', repo
            ],
            check=True,
            env={**os.environ, 'GITHUB_TOKEN': token}
        )
        print(f"Uploaded attestation.json to release")

        return f"https://github.com/{repo}/releases/tag/{tag}"

    except subprocess.CalledProcessError as e:
        print(f"Error creating release: {e.stderr}", file=sys.stderr)
        raise


def main():
    """Create release from environment variables."""

    quote = os.environ.get('QUOTE')
    endpoint = os.environ.get('ENDPOINT')
    measurements = os.environ.get('MEASUREMENTS', '{}')

    if not quote:
        print("Warning: No TDX quote available, creating release without attestation")
        quote = ""

    if not endpoint:
        print("Error: ENDPOINT must be set", file=sys.stderr)
        sys.exit(1)

    try:
        url = create_release(quote, endpoint, measurements)
        print(f"Release created: {url}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
