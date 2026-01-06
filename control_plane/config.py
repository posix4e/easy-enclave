from __future__ import annotations

import os


def env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value


BIND_HOST = env("EE_CONTROL_BIND", "0.0.0.0")
BIND_PORT = int(env("EE_CONTROL_PORT", "8088"))
ALLOWLIST_ASSET = env("EE_ALLOWLIST_ASSET", "agent-attestation-allowlist.json")
GITHUB_TOKEN = env("EE_GITHUB_TOKEN")
PCCS_URL = env("EE_PCCS_URL")
ADMIN_TOKEN = env("EE_ADMIN_TOKEN")

ATTEST_INTERVAL_SEC = int(env("EE_ATTEST_INTERVAL_SEC", "3600"))
ATTEST_DEADLINE_SEC = int(env("EE_ATTEST_DEADLINE_SEC", "30"))
REGISTRATION_TTL_DAYS = int(env("EE_REGISTRATION_TTL_DAYS", "30"))
REGISTRATION_WARN_DAYS = int(env("EE_REGISTRATION_WARN_DAYS", "3"))
