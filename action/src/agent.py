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
import threading
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional

# Import from vm.py
from vm import (
    clone_repo,
    find_docker_compose,
    create_td_vm,
    create_release,
    check_requirements,
    log,
    DEPLOYMENTS_DIR,
)

# Force unbuffered output
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)


@dataclass
class Deployment:
    """Deployment state."""
    id: str
    repo: str
    ref: Optional[str]
    docker_compose: Optional[str]
    port: int
    status: str  # pending, cloning, deploying, complete, failed
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
        json.dump(asdict(deployment), f, indent=2)


def load_deployment(deployment_id: str) -> Optional[Deployment]:
    """Load deployment state from file."""
    path = DEPLOYMENTS_DIR / f"{deployment_id}.json"
    if not path.exists():
        return None
    with open(path) as f:
        data = json.load(f)
    return Deployment(**data)


def run_deployment(deployment: Deployment, token: str):
    """Run deployment in background thread."""
    try:
        # Clone repo
        deployment.status = "cloning"
        save_deployment(deployment)
        repo_path = clone_repo(deployment.repo, ref=deployment.ref, token=token)
        docker_compose = find_docker_compose(repo_path, hint=deployment.docker_compose)

        # Deploy VM
        deployment.status = "deploying"
        save_deployment(deployment)
        result = create_td_vm(
            docker_compose,
            name=deployment.vm_name or f"ee-{deployment.id[:8]}",
            port=deployment.port,
            enable_ssh=False,
        )

        # Update deployment with results
        deployment.vm_name = result.get('name')
        deployment.vm_ip = result.get('ip')
        deployment.quote = result.get('quote')

        # Create release
        endpoint = f"http://{deployment.vm_ip}:{deployment.port}"
        # Set environment for create_release
        os.environ['GITHUB_REPOSITORY'] = deployment.repo
        if token:
            os.environ['GITHUB_TOKEN'] = token
        release_url = create_release(deployment.quote, endpoint)
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
            self.send_json(asdict(deployment))
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
            deployment = Deployment(
                id=str(uuid.uuid4()),
                repo=repo,
                ref=data.get('ref'),
                docker_compose=data.get('docker_compose'),
                port=data.get('port', 8080),
                status='pending',
                vm_name=data.get('vm_name'),
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
