from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional


@dataclass
class AppRecord:
    app_name: str
    repo: str
    release_tag: str
    network: str
    agent_id: str
    registered_at: datetime
    registration_expires_at: datetime
    last_attested_at: Optional[datetime] = None
    last_health_at: Optional[datetime] = None
    sealed: bool = False
    attestation_status: str = "unknown"
    health_status: str = "unknown"
    ws_connected: bool = False
    tunnel_id: Optional[str] = None

    def ttl_warning_at(self, warn_days: int) -> datetime:
        return self.registration_expires_at - timedelta(days=warn_days)

    def registration_state(self, warn_days: int) -> str:
        now = datetime.now(timezone.utc)
        if now >= self.registration_expires_at:
            return "expired"
        if now >= self.ttl_warning_at(warn_days):
            return "warning"
        return "active"


@dataclass
class RegistryConfig:
    ttl_days: int
    warn_days: int


class Registry:
    def __init__(self, config: RegistryConfig) -> None:
        self._config = config
        self._apps: dict[str, AppRecord] = {}

    def get(self, app_name: str) -> Optional[AppRecord]:
        return self._apps.get(app_name)

    def list_apps(self) -> list[AppRecord]:
        return sorted(self._apps.values(), key=lambda item: item.app_name)

    def register(
        self,
        app_name: str,
        repo: str,
        release_tag: str,
        network: str,
        agent_id: str,
    ) -> AppRecord:
        now = datetime.now(timezone.utc)
        record = self._apps.get(app_name)
        if record and record.repo != repo:
            raise ValueError("app_name already bound to a different repo")

        registration_expires_at = now + timedelta(days=self._config.ttl_days)
        if record:
            record.release_tag = release_tag
            record.network = network
            record.agent_id = agent_id
            record.registered_at = now
            record.registration_expires_at = registration_expires_at
        else:
            record = AppRecord(
                app_name=app_name,
                repo=repo,
                release_tag=release_tag,
                network=network,
                agent_id=agent_id,
                registered_at=now,
                registration_expires_at=registration_expires_at,
            )
            self._apps[app_name] = record
        return record

    def mark_attested(self, app_name: str, sealed: bool, status: str) -> None:
        record = self._apps[app_name]
        record.last_attested_at = datetime.now(timezone.utc)
        record.sealed = sealed
        record.attestation_status = status

    def mark_health(self, app_name: str, status: str) -> None:
        record = self._apps[app_name]
        record.last_health_at = datetime.now(timezone.utc)
        record.health_status = status

    def mark_connection(self, app_name: str, connected: bool, tunnel_id: Optional[str]) -> None:
        record = self._apps[app_name]
        record.ws_connected = connected
        record.tunnel_id = tunnel_id

    def status_payload(self, record: AppRecord) -> dict:
        state = record.registration_state(self._config.warn_days)
        allowed = (
            state == "active"
            and record.attestation_status == "valid"
            and record.health_status == "pass"
            and record.ws_connected
        )
        if record.network == "prod" and not record.sealed:
            allowed = False
        if state == "expired":
            allowed = False
        return {
            "app_name": record.app_name,
            "repo": record.repo,
            "release_tag": record.release_tag,
            "network": record.network,
            "agent_id": record.agent_id,
            "registered_at": record.registered_at.isoformat(),
            "registration_expires_at": record.registration_expires_at.isoformat(),
            "registration_state": state,
            "sealed": record.sealed,
            "attestation_status": record.attestation_status,
            "health_status": record.health_status,
            "ws_connected": record.ws_connected,
            "last_attested_at": record.last_attested_at.isoformat() if record.last_attested_at else None,
            "last_health_at": record.last_health_at.isoformat() if record.last_health_at else None,
            "allowed": allowed,
        }
