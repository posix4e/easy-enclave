#!/usr/bin/env python3
"""
Easy Enclave Agent (single process)

HTTP API + attestation + WS tunnel client in one process.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from aiohttp import ClientSession, WSMsgType, web
from vm import (
    DEPLOYMENTS_DIR,
    check_requirements,
    cleanup_td_vms,
    create_release,
    create_td_vm,
    get_public_ip,
    log,
    setup_port_forward,
)

# Force unbuffered output
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

EE_CONTROL_WS = os.getenv("EE_CONTROL_WS", "")
EE_REPO = os.getenv("EE_REPO", "")
EE_RELEASE_TAG = os.getenv("EE_RELEASE_TAG", "")
EE_APP_NAME = os.getenv("EE_APP_NAME", "")
EE_NETWORK = os.getenv("EE_NETWORK", "forge-1")
EE_AGENT_ID = os.getenv("EE_AGENT_ID", str(uuid.uuid4()))
EE_BACKEND_URL = os.getenv("EE_BACKEND_URL", "http://127.0.0.1:8080")
EE_HEALTH_INTERVAL_SEC = int(os.getenv("EE_HEALTH_INTERVAL_SEC", "60"))
EE_RECONNECT_DELAY_SEC = int(os.getenv("EE_RECONNECT_DELAY_SEC", "5"))


@dataclass
class Deployment:
    """Deployment state."""

    id: str
    repo: str
    port: int
    status: str  # pending, cloning, deploying, complete, failed
    cleanup_prefixes: Optional[list[str]] = None
    bundle_artifact_id: Optional[int] = None
    private_env: Optional[str] = None
    seal_vm: bool = False
    vm_name: Optional[str] = None
    vm_ip: Optional[str] = None
    quote: Optional[str] = None
    release_url: Optional[str] = None
    error: Optional[str] = None
    created_at: str = None
    updated_at: str = None

    def __post_init__(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        self.updated_at = now


def ensure_deployments_dir() -> None:
    """Ensure deployments directory exists."""

    DEPLOYMENTS_DIR.mkdir(parents=True, exist_ok=True)


def save_deployment(deployment: Deployment) -> None:
    """Save deployment state to file."""

    ensure_deployments_dir()
    deployment.updated_at = datetime.now(timezone.utc).isoformat()
    path = DEPLOYMENTS_DIR / f"{deployment.id}.json"
    with open(path, "w") as f:
        data = asdict(deployment)
        data.pop("private_env", None)
        json.dump(data, f, indent=2)


def load_deployment(deployment_id: str) -> Optional[Deployment]:
    """Load deployment state from file."""

    path = DEPLOYMENTS_DIR / f"{deployment_id}.json"
    if not path.exists():
        return None
    with open(path) as f:
        data = json.load(f)
    fields = Deployment.__annotations__.keys()
    filtered = {key: value for key, value in data.items() if key in fields}
    return Deployment(**filtered)


def read_tail(path: str, max_bytes: int = 20000) -> str:
    """Read the tail of a log file."""

    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            start = max(size - max_bytes, 0)
            f.seek(start)
            data = f.read()
        return data.decode(errors="replace")
    except FileNotFoundError:
        return ""
    except Exception as e:
        return f"[log read error: {e}]"


def sha256_file(path: Path) -> str:
    """Hash a file using SHA256."""

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_dir(root: Path) -> str:
    """Hash a directory tree deterministically."""

    h = hashlib.sha256()
    skip_names = {"__pycache__", ".git", "deployments", "tmp"}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if any(part in skip_names for part in path.parts):
            continue
        rel = path.relative_to(root).as_posix().encode()
        h.update(rel + b"\n")
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
    return h.hexdigest()


def get_vm_image_id() -> str:
    """Get the VM image identifier used for attestation."""

    env_id = os.environ.get("VM_IMAGE_ID")
    if env_id:
        return env_id
    path = Path("/etc/easy-enclave/vm_image_id")
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    raise RuntimeError("VM_IMAGE_ID not set")


def get_sealed_state() -> bool:
    """Return sealed state based on environment."""

    value = os.environ.get("SEAL_VM", "").lower()
    return value in ("1", "true", "yes")


def build_report_data(measurements: dict) -> bytes:
    """Build 64-byte report data from measurements."""

    material = (
        f"agent_dir={measurements['agent_dir_sha256']}\n"
        f"agent_py={measurements['agent_py_sha256']}\n"
        f"vm_image_id={measurements['vm_image_id']}\n"
        f"sealed={str(measurements['sealed']).lower()}"
    ).encode()
    digest = hashlib.sha256(material).digest()
    return digest + b"\x00" * 32


def get_tdx_quote(report_data: bytes) -> bytes:
    """Get a TDX quote from configfs-tsm."""

    tsm_path = Path("/sys/kernel/config/tsm/report")
    if not tsm_path.exists():
        raise RuntimeError(f"configfs-tsm not available at {tsm_path}")
    report_dir = tempfile.mkdtemp(dir=tsm_path)
    inblob = Path(report_dir) / "inblob"
    outblob = Path(report_dir) / "outblob"
    with open(inblob, "wb") as f:
        f.write(report_data.ljust(64, b"\x00")[:64])
    with open(outblob, "rb") as f:
        data = f.read()
    if len(data) == 0:
        raise RuntimeError("Empty quote from configfs-tsm")
    return data


def build_attestation() -> dict:
    """Build attestation payload for the agent."""

    agent_path = Path(__file__).resolve()
    agent_dir = Path(os.environ.get("EE_AGENT_DIR", agent_path.parent))
    measurements = {
        "agent_dir_sha256": sha256_dir(agent_dir),
        "agent_py_sha256": sha256_file(agent_path),
        "vm_image_id": get_vm_image_id(),
        "sealed": get_sealed_state(),
    }
    report_data = build_report_data(measurements)
    quote = get_tdx_quote(report_data)
    return {
        "quote": base64.b64encode(quote).decode(),
        "report_data": report_data.hex(),
        "measurements": measurements,
    }


def is_vm_mode() -> bool:
    """Return true if agent is running inside the agent VM."""

    return os.environ.get("EE_AGENT_MODE", "").lower() == "vm"


def write_bundle_files(bundle_dir: str, extra_files: list[dict[str, str]]) -> str:
    """Write bundle files to /opt/workload and return compose path."""

    target_root = Path("/opt/workload")
    target_root.mkdir(parents=True, exist_ok=True)
    compose_path = target_root / "docker-compose.yml"
    src_compose = Path(bundle_dir) / "docker-compose.yml"
    if not src_compose.exists():
        src_compose = Path(bundle_dir) / "docker-compose.yaml"
    compose_path.write_text(src_compose.read_text(encoding="utf-8"), encoding="utf-8")

    for entry in extra_files:
        rel_path = entry.get("path")
        if not rel_path:
            continue
        dest_path = target_root / rel_path.lstrip("/")
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_text(entry.get("content", ""), encoding="utf-8")
        if entry.get("permissions"):
            os.chmod(dest_path, int(entry["permissions"], 8))
    return str(compose_path)


def resolve_compose_command() -> list[str]:
    """Return a compose command that exists on this host."""

    result = subprocess.run(
        ["docker", "compose", "version"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return ["docker", "compose"]
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    raise RuntimeError("docker compose is not available in the agent VM")


def run_docker_compose(compose_path: str) -> None:
    """Run docker compose to start workload."""

    compose_cmd = resolve_compose_command()
    result = subprocess.run(
        [*compose_cmd, "-f", compose_path, "up", "-d", "--remove-orphans"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"docker compose failed: {result.stderr.strip()}")


def download_bundle_artifact(repo: str, artifact_id: int, token: Optional[str]) -> str:
    """Download and extract a bundle artifact, returning the extract directory."""

    tmpdir = tempfile.mkdtemp(prefix="ee-bundle-")
    zip_path = os.path.join(tmpdir, "bundle.zip")
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "easy-enclave-agent",
    }
    if token:
        headers["Authorization"] = f"token {token}"

    url = f"https://api.github.com/repos/{repo}/actions/artifacts/{artifact_id}/zip"

    class NoAuthRedirectHandler(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            new_req = super().redirect_request(req, fp, code, msg, hdrs, newurl)
            if new_req is None:
                return None
            old_host = urlparse(req.full_url).netloc
            new_host = urlparse(new_req.full_url).netloc
            if old_host != new_host:
                new_req.headers.pop("Authorization", None)
            return new_req

    req = Request(url, headers=headers)
    opener = build_opener(NoAuthRedirectHandler())
    with opener.open(req) as response, open(zip_path, "wb") as f:
        f.write(response.read())

    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(tmpdir)

    return tmpdir


def load_bundle(bundle_dir: str) -> tuple[str, list[dict[str, str]], dict]:
    """Load docker-compose and extra files from the bundle."""

    root = Path(bundle_dir)
    compose_paths = list(root.rglob("docker-compose.yml")) + list(root.rglob("docker-compose.yaml"))
    if not compose_paths:
        raise FileNotFoundError("Bundle missing docker-compose.yml")
    if len(compose_paths) > 1:
        raise ValueError("Bundle has multiple docker-compose files")
    compose_path = compose_paths[0]

    env_public = None
    if (root / ".env.public").exists():
        env_public = (root / ".env.public").read_text(encoding="utf-8")

    authorized_keys = None
    if (root / "authorized_keys").exists():
        authorized_keys = (root / "authorized_keys").read_text(encoding="utf-8")

    extra_files = []
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        if path.name in {"docker-compose.yml", "docker-compose.yaml", ".env.public", "authorized_keys", "bundle.zip"}:
            continue
        rel = path.relative_to(root).as_posix()
        extra_files.append({"path": rel, "content": path.read_text(encoding="utf-8")})

    return str(compose_path), extra_files, {
        "env_public": env_public,
        "authorized_keys": authorized_keys,
    }


def run_deployment(deployment: Deployment, token: Optional[str]) -> None:
    """Background worker to execute deployment."""

    try:
        deployment.status = "deploying"
        save_deployment(deployment)

        log(f"Downloading bundle artifact {deployment.bundle_artifact_id} for {deployment.repo}...")
        bundle_dir = download_bundle_artifact(deployment.repo, deployment.bundle_artifact_id, token)

        compose_path, extra_files, bundle_meta = load_bundle(bundle_dir)

        if deployment.cleanup_prefixes:
            cleanup_td_vms(deployment.cleanup_prefixes)

        if is_vm_mode():
            compose_path = write_bundle_files(bundle_dir, extra_files)
            if bundle_meta.get("env_public"):
                with open("/opt/workload/.env.public", "w") as f:
                    f.write(bundle_meta["env_public"])
            if deployment.private_env:
                with open("/opt/workload/.env.private", "w") as f:
                    f.write(deployment.private_env)
                os.chmod("/opt/workload/.env.private", 0o600)
            env_path = "/opt/workload/.env"
            parts = []
            if bundle_meta.get("env_public"):
                parts.append("/opt/workload/.env.public")
            if deployment.private_env:
                parts.append("/opt/workload/.env.private")
            if parts:
                with open(env_path, "w") as f:
                    for idx, part in enumerate(parts):
                        if idx:
                            f.write("\n")
                        f.write(Path(part).read_text(encoding="utf-8"))

            if bundle_meta.get("authorized_keys"):
                os.makedirs("/home/ubuntu/.ssh", exist_ok=True)
                with open("/home/ubuntu/.ssh/authorized_keys", "w") as f:
                    f.write(bundle_meta["authorized_keys"])
                os.chmod("/home/ubuntu/.ssh/authorized_keys", 0o600)

            run_docker_compose(compose_path)
            deployment.status = "complete"
            save_deployment(deployment)
            return

        log(f"Creating TD VM with docker-compose from {compose_path}...")
        result = create_td_vm(
            compose_path,
            name=deployment.vm_name or f"ee-deploy-{deployment.id}",
            port=deployment.port,
            enable_ssh=False,
            extra_files=extra_files,
        )

        deployment.vm_name = result.get("name")
        deployment.vm_ip = result.get("ip")
        deployment.quote = result.get("quote")

        endpoint = f"http://{deployment.vm_ip}:{deployment.port}"
        public_ip = get_public_ip()
        if public_ip:
            setup_port_forward(deployment.vm_ip, deployment.port, deployment.port)
            endpoint = f"http://{public_ip}:{deployment.port}"

        release_url = create_release(deployment.quote, endpoint, repo=deployment.repo, token=token, seal_vm=deployment.seal_vm)
        deployment.release_url = release_url
        deployment.status = "complete"
        save_deployment(deployment)

    except Exception as exc:
        deployment.status = "failed"
        deployment.error = str(exc)
        save_deployment(deployment)
        log(f"Deployment failed: {exc}")


def require_bearer_token(request: web.Request) -> Optional[str]:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:]
    if auth_header.lower().startswith("token "):
        return auth_header[6:]
    if auth_header:
        return auth_header.strip()
    return None


async def handle_health(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def handle_attestation(_: web.Request) -> web.Response:
    try:
        payload = build_attestation()
        return web.json_response(payload)
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def handle_status(request: web.Request) -> web.Response:
    deployment_id = request.match_info["deployment_id"]
    deployment = load_deployment(deployment_id)
    if not deployment:
        return web.json_response({"error": "Deployment not found"}, status=404)
    payload = asdict(deployment)
    if deployment.vm_name:
        qemu_log = f"/var/log/libvirt/qemu/{deployment.vm_name}.log"
        serial_log = f"/var/log/libvirt/qemu/{deployment.vm_name}-serial.log"
        payload["host_logs"] = {
            "qemu": read_tail(qemu_log),
            "serial": read_tail(serial_log),
        }
    return web.json_response(payload)


async def handle_deploy(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    repo = data.get("repo")
    if not repo:
        return web.json_response({"error": "Missing required field: repo"}, status=400)

    cleanup_prefixes = data.get("cleanup_prefixes")
    if cleanup_prefixes is not None:
        if not isinstance(cleanup_prefixes, list) or not all(isinstance(p, str) for p in cleanup_prefixes):
            return web.json_response({"error": "cleanup_prefixes must be a list of strings"}, status=400)

    bundle_artifact_id = data.get("bundle_artifact_id")
    if not isinstance(bundle_artifact_id, int):
        return web.json_response({"error": "bundle_artifact_id must be an integer"}, status=400)

    private_env = data.get("private_env")
    if private_env is not None and not isinstance(private_env, str):
        return web.json_response({"error": "private_env must be a string"}, status=400)

    seal_vm = data.get("seal_vm", False)
    if not isinstance(seal_vm, bool):
        return web.json_response({"error": "seal_vm must be a boolean"}, status=400)

    deployment = Deployment(
        id=str(uuid.uuid4()),
        repo=repo,
        port=data.get("port", 8080),
        status="pending",
        vm_name=data.get("vm_name"),
        cleanup_prefixes=cleanup_prefixes,
        bundle_artifact_id=bundle_artifact_id,
        private_env=private_env,
        seal_vm=seal_vm,
    )
    save_deployment(deployment)

    token = require_bearer_token(request)
    thread = threading.Thread(
        target=run_deployment,
        args=(deployment, token),
        daemon=True,
    )
    thread.start()

    return web.json_response({"deployment_id": deployment.id, "status": deployment.status}, status=202)


async def proxy_request(session: ClientSession, message: dict) -> dict:
    request_id = message.get("request_id")
    method = message.get("method", "GET")
    path = message.get("path", "/")
    headers = message.get("headers") or {}
    body_b64 = message.get("body_b64") or ""
    body = base64.b64decode(body_b64.encode("ascii")) if body_b64 else b""

    url = urljoin(EE_BACKEND_URL, path.lstrip("/"))
    async with session.request(method, url, headers=headers, data=body) as resp:
        response_body = await resp.read()
        return {
            "type": "proxy_response",
            "request_id": request_id,
            "status": resp.status,
            "headers": dict(resp.headers),
            "body_b64": base64.b64encode(response_body).decode("ascii"),
        }


async def health_loop(ws) -> None:
    while not ws.closed:
        await asyncio.sleep(EE_HEALTH_INTERVAL_SEC)
        if ws.closed:
            return
        await ws.send_json({"type": "health", "status": "pass"})


async def tunnel_client_loop(app: web.Application) -> None:
    if not EE_CONTROL_WS:
        log("EE_CONTROL_WS not set; tunnel client disabled")
        return
    if not EE_REPO or not EE_RELEASE_TAG or not EE_APP_NAME:
        log("EE_REPO, EE_RELEASE_TAG, EE_APP_NAME required for tunnel client")
        return

    while True:
        try:
            async with ClientSession() as session:
                async with session.ws_connect(EE_CONTROL_WS) as ws:
                    await ws.send_json(
                        {
                            "type": "register",
                            "repo": EE_REPO,
                            "release_tag": EE_RELEASE_TAG,
                            "app_name": EE_APP_NAME,
                            "network": EE_NETWORK,
                            "agent_id": EE_AGENT_ID,
                            "tunnel_version": "1",
                        }
                    )
                    health_task = asyncio.create_task(health_loop(ws))
                    async for msg in ws:
                        if msg.type == WSMsgType.TEXT:
                            payload = msg.json()
                            msg_type = payload.get("type")
                            if msg_type == "attest_request":
                                attestation = build_attestation()
                                await ws.send_json(
                                    {
                                        "type": "attest_response",
                                        "nonce": payload.get("nonce"),
                                        "quote": attestation.get("quote"),
                                        "report_data": attestation.get("report_data"),
                                        "measurements": attestation.get("measurements"),
                                    }
                                )
                            elif msg_type == "proxy_request":
                                response = await proxy_request(session, payload)
                                await ws.send_json(response)
                                await ws.send_json({"type": "health", "status": "pass"})
                            elif msg_type == "status":
                                log(f"tunnel status: {payload.get('state')} {payload.get('reason')}")
                        elif msg.type == WSMsgType.ERROR:
                            break
                    health_task.cancel()
        except Exception as exc:
            log(f"tunnel_error={exc}")
        await asyncio.sleep(EE_RECONNECT_DELAY_SEC)


def build_app() -> web.Application:
    app = web.Application()
    app.add_routes(
        [
            web.get("/health", handle_health),
            web.get("/attestation", handle_attestation),
            web.get("/status/{deployment_id}", handle_status),
            web.post("/deploy", handle_deploy),
        ]
    )

    async def start_tunnel(_: web.Application) -> None:
        asyncio.create_task(tunnel_client_loop(app))

    app.on_startup.append(start_tunnel)
    return app


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Easy Enclave Deployment Agent")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on")
    parser.add_argument("--check", action="store_true", help="Check TDX requirements and exit")
    args = parser.parse_args()

    if args.check:
        try:
            check_requirements()
            print("All requirements met")
            sys.exit(0)
        except Exception as e:
            print(f"Requirements check failed: {e}")
            sys.exit(1)

    ensure_deployments_dir()
    log(f"Starting agent on {args.host}:{args.port}")
    log(f"Deployments directory: {DEPLOYMENTS_DIR}")

    app = build_app()
    web.run_app(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
