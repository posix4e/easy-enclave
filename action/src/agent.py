#!/usr/bin/env python3
"""
Easy Enclave Deployment Agent

HTTP server that receives deployment requests and runs them asynchronously.
Deployments are tracked in /var/lib/easy-enclave/deployments/.

Usage:
    python agent.py [--port 8000] [--host 0.0.0.0]

API:
    POST /deploy - Start a new deployment
    GET /status/{id} - Get deployment status
    GET /health - Health check
"""

import json
import os
import sys
import tempfile
import threading
import uuid
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen

from vm import (
    DEPLOYMENTS_DIR,
    check_requirements,
    create_release,
    create_td_vm,
    cleanup_td_vms,
    get_public_ip,
    log,
    setup_port_forward,
)

# Force unbuffered output
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)


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

    def __post_init__(self):
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        self.updated_at = now


def ensure_deployments_dir():
    """Ensure deployments directory exists."""
    DEPLOYMENTS_DIR.mkdir(parents=True, exist_ok=True)


def save_deployment(deployment: Deployment):
    """Save deployment state to file."""
    ensure_deployments_dir()
    deployment.updated_at = datetime.now(timezone.utc).isoformat()
    path = DEPLOYMENTS_DIR / f"{deployment.id}.json"
    with open(path, 'w') as f:
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


def download_bundle_artifact(repo: str, artifact_id: int, token: Optional[str]) -> str:
    """Download and extract a bundle artifact, returning the extract directory."""
    tmpdir = tempfile.mkdtemp(prefix="ee-bundle-")
    zip_path = os.path.join(tmpdir, "bundle.zip")
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"https://api.github.com/repos/{repo}/actions/artifacts/{artifact_id}/zip"
    req = Request(url, headers=headers)
    with urlopen(req) as response, open(zip_path, "wb") as f:
        f.write(response.read())

    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(tmpdir)

    return tmpdir


def load_bundle(bundle_dir: str, private_env: Optional[str]) -> tuple[str, list[dict[str, str]]]:
    """Load docker-compose and extra files from the bundle."""
    root = Path(bundle_dir)
    compose_paths = list(root.rglob("docker-compose.yml")) + list(root.rglob("docker-compose.yaml"))
    if not compose_paths:
        raise FileNotFoundError("Bundle missing docker-compose.yml")
    if len(compose_paths) > 1:
        raise ValueError("Bundle has multiple docker-compose files")
    compose_path = compose_paths[0]
    docker_compose_content = compose_path.read_text(encoding="utf-8")

    public_env = None
    extra_files = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.name == "bundle.zip":
            continue
        if path == compose_path:
            continue
        if path.name in (".env.public", ".env"):
            public_env = path.read_text(encoding="utf-8")
            continue
        rel_path = path.relative_to(root)
        extra_files.append({
            "path": str(rel_path),
            "content": path.read_text(encoding="utf-8"),
        })

    env_parts = []
    if public_env:
        env_parts.append(public_env.rstrip())
    if private_env:
        env_parts.append(private_env.rstrip())
    if env_parts:
        combined = "\n".join(part for part in env_parts if part) + "\n"
        extra_files.append({
            "path": ".env",
            "content": combined,
            "permissions": "0600",
        })

    return docker_compose_content, extra_files


def run_deployment(deployment: Deployment, token: str):
    """Run deployment in background thread."""
    try:
        if not deployment.bundle_artifact_id:
            raise ValueError("bundle_artifact_id is required")

        deployment.status = "cloning"
        save_deployment(deployment)
        bundle_dir = download_bundle_artifact(deployment.repo, deployment.bundle_artifact_id, token)
        docker_compose_content, extra_files = load_bundle(bundle_dir, deployment.private_env)
        docker_compose_path = os.path.join(bundle_dir, "docker-compose.yml")
        with open(docker_compose_path, "w", encoding="utf-8") as f:
            f.write(docker_compose_content)

        # Deploy VM
        deployment.status = "deploying"
        save_deployment(deployment)
        cleanup_td_vms(deployment.cleanup_prefixes)
        result = create_td_vm(
            docker_compose_path,
            name=deployment.vm_name or f"ee-deploy-{deployment.id[:8]}",
            port=deployment.port,
            enable_ssh=False,
            extra_files=extra_files,
        )

        # Update deployment with results
        deployment.vm_name = result.get('name')
        deployment.vm_ip = result.get('ip')
        deployment.quote = result.get('quote')

        # Set up port forwarding from host to VM
        log("Setting up port forwarding...")
        setup_port_forward(deployment.vm_ip, deployment.port)

        # Get public IP for endpoint
        public_ip = get_public_ip()
        log(f"Public IP: {public_ip}")

        # Create release with public endpoint
        endpoint = f"http://{public_ip}:{deployment.port}"
        # Set environment for create_release
        os.environ['GITHUB_REPOSITORY'] = deployment.repo
        if token:
            os.environ['GITHUB_TOKEN'] = token
        release_url = create_release(deployment.quote, endpoint, seal_vm=deployment.seal_vm)
        deployment.release_url = release_url

        deployment.status = "complete"
        save_deployment(deployment)
        log(f"Deployment {deployment.id} complete: {release_url}")

    except Exception as e:
        deployment.status = "failed"
        deployment.error = str(e)
        save_deployment(deployment)
        log(f"Deployment {deployment.id} failed: {e}")


class AgentHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the agent."""

    def log_message(self, format, *args):
        """Log to stderr instead of stdout."""
        log(f"{self.address_string()} - {format % args}")

    def send_json(self, data: dict, status: int = 200):
        """Send JSON response."""
        body = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        """Handle GET requests."""
        if self.path == '/health':
            self.send_json({"status": "ok"})
            return

        if self.path.startswith('/status/'):
            deployment_id = self.path.split('/status/')[-1]
            deployment = load_deployment(deployment_id)
            if not deployment:
                self.send_json({"error": "Deployment not found"}, status=404)
                return
            payload = asdict(deployment)
            if deployment.vm_name:
                qemu_log = f"/var/log/libvirt/qemu/{deployment.vm_name}.log"
                serial_log = f"/var/log/libvirt/qemu/{deployment.vm_name}-serial.log"
                payload["host_logs"] = {
                    "qemu": read_tail(qemu_log),
                    "serial": read_tail(serial_log),
                }
            self.send_json(payload)
            return

        self.send_json({"error": "Not found"}, status=404)

    def do_POST(self):
        """Handle POST requests."""
        if self.path == '/deploy':
            # Read request body
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode()
            try:
                data = json.loads(body) if body else {}
            except json.JSONDecodeError:
                self.send_json({"error": "Invalid JSON"}, status=400)
                return

            # Validate required fields
            repo = data.get('repo')
            if not repo:
                self.send_json({"error": "Missing required field: repo"}, status=400)
                return

            # Get auth token from header
            auth_header = self.headers.get('Authorization', '')
            token = None
            if auth_header.startswith('Bearer '):
                token = auth_header[7:]

            # Create deployment
            cleanup_prefixes = data.get('cleanup_prefixes')
            if cleanup_prefixes is not None:
                if not isinstance(cleanup_prefixes, list) or not all(isinstance(p, str) for p in cleanup_prefixes):
                    self.send_json({"error": "cleanup_prefixes must be a list of strings"}, status=400)
                    return
            bundle_artifact_id = data.get('bundle_artifact_id')
            if not isinstance(bundle_artifact_id, int):
                self.send_json({"error": "bundle_artifact_id must be an integer"}, status=400)
                return
            private_env = data.get('private_env')
            if private_env is not None and not isinstance(private_env, str):
                self.send_json({"error": "private_env must be a string"}, status=400)
                return
            seal_vm = data.get('seal_vm', False)
            if not isinstance(seal_vm, bool):
                self.send_json({"error": "seal_vm must be a boolean"}, status=400)
                return
            deployment = Deployment(
                id=str(uuid.uuid4()),
                repo=repo,
                port=data.get('port', 8080),
                status='pending',
                vm_name=data.get('vm_name'),
                cleanup_prefixes=cleanup_prefixes,
                bundle_artifact_id=bundle_artifact_id,
                private_env=private_env,
                seal_vm=seal_vm,
            )
            save_deployment(deployment)

            # Start deployment in background
            thread = threading.Thread(
                target=run_deployment,
                args=(deployment, token),
                daemon=True,
            )
            thread.start()

            self.send_json({
                "deployment_id": deployment.id,
                "status": deployment.status,
            }, status=202)
            return

        self.send_json({"error": "Not found"}, status=404)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Easy Enclave Deployment Agent')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to')
    parser.add_argument('--port', type=int, default=8000, help='Port to listen on')
    parser.add_argument('--check', action='store_true', help='Check TDX requirements and exit')
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

    server = HTTPServer((args.host, args.port), AgentHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("Shutting down...")
        server.shutdown()


if __name__ == '__main__':
    main()
