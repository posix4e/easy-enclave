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
import socket
import ssl
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
import uuid
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from aiohttp import ClientSession, WSMsgType, web
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from ratls import build_ratls_cert, report_data_for_pubkey, verify_ratls_cert

# Force unbuffered output
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# VM + host utilities (embedded to avoid separate vm.py dependency).
DEPLOYMENTS_DIR = Path("/var/lib/easy-enclave/deployments")

_script_dir = Path(__file__).parent
_possible_template_dirs = [
    _script_dir / "templates",
    _script_dir.parent / "installer" / "templates",
]
TEMPLATES_DIR = next((d for d in _possible_template_dirs if d.exists()), _possible_template_dirs[0])

TDX_REPO_URL = "https://github.com/canonical/tdx.git"
DEFAULT_TDX_REPO_DIR = "/var/lib/easy-enclave/tdx"
DEFAULT_TDX_GUEST_VERSION = "24.04"
IMAGE_DIR = "/var/lib/easy-enclave"
DEFAULT_TD_IMAGE = f"{IMAGE_DIR}/td-guest.qcow2"
UBUNTU_CLOUD_IMAGES = {
    "24.04": "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img",
    "24.10": "https://cloud-images.ubuntu.com/oracular/current/oracular-server-cloudimg-amd64.img",
}


def log(msg: str) -> None:
    """Print to stderr for logging (keeps stdout clean for JSON output)."""
    print(msg, file=sys.stderr)


def load_template(name: str) -> str:
    """Load a template file from the templates directory."""
    return (TEMPLATES_DIR / name).read_text()


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def check_requirements() -> None:
    """Check that TDX and libvirt are available. Fails fast if not."""
    result = subprocess.run(["uname", "-r"], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("Cannot get kernel version")
    kernel = result.stdout.strip()
    log(f"Kernel: {kernel}")

    tdx_enabled = False
    tdx_path = "/sys/module/kvm_intel/parameters/tdx"
    if os.path.exists(tdx_path):
        with open(tdx_path) as f:
            if f.read().strip() in ("Y", "1"):
                tdx_enabled = True
    if not tdx_enabled:
        raise RuntimeError(f"TDX not enabled (check {tdx_path})")
    log("TDX: enabled")

    result = subprocess.run(["virsh", "version"], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("libvirt not available (virsh not found)")
    log("libvirt: available")

    result = subprocess.run(["virsh", "domcapabilities", "--machine", "q35"], capture_output=True, text=True)
    if "tdx" not in result.stdout.lower():
        raise RuntimeError("libvirt does not support TDX (check QEMU/libvirt versions)")
    log("libvirt TDX: supported")


def find_existing_images() -> list:
    """Find existing TD/cloud images on the system."""
    images = []
    search_paths = [
        "/var/lib/libvirt/images",
        "/var/lib/easy-enclave",
        os.path.expanduser("~/tdx/guest-tools/image"),
        "/opt/tdx/guest-tools/image",
        "/home/ubuntu/tdx/guest-tools/image",
    ]
    patterns = ["*.qcow2", "*.img"]
    for search_path in search_paths:
        if os.path.isdir(search_path):
            for pattern in patterns:
                import glob
                for img in glob.glob(os.path.join(search_path, pattern)):
                    try:
                        size = os.path.getsize(img)
                        images.append({
                            "path": img,
                            "size_gb": round(size / (1024**3), 2),
                            "name": os.path.basename(img),
                        })
                    except Exception:
                        pass
    return images


def ensure_tdx_repo(repo_dir: str, ref: str = "main") -> Path:
    """Ensure the canonical/tdx repo is available locally."""
    repo_path = Path(repo_dir)
    guest_tools = repo_path / "guest-tools" / "image" / "create-td-image.sh"
    if guest_tools.exists():
        return repo_path
    if repo_path.exists() and not (repo_path / ".git").exists():
        raise RuntimeError(f"TDX repo path exists but is not a git repo: {repo_path}")
    repo_path.parent.mkdir(parents=True, exist_ok=True)
    log(f"Cloning canonical/tdx into {repo_path}...")
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", ref, TDX_REPO_URL, str(repo_path)],
        check=True,
        capture_output=True,
    )
    return repo_path


def find_latest_td_image(image_dir: Path, version: str) -> str:
    """Find the most recent TD image in the given directory."""
    candidates = sorted(
        image_dir.glob(f"tdx-guest-ubuntu-{version}-*.qcow2"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise RuntimeError(f"No TD image found in {image_dir} for Ubuntu {version}")
    return str(candidates[0])


def build_td_image_from_repo(
    repo_dir: str,
    version: str = DEFAULT_TDX_GUEST_VERSION,
    ref: str = "main",
) -> str:
    """Build a TD guest image using canonical/tdx tooling."""
    repo_path = ensure_tdx_repo(repo_dir, ref=ref)
    image_dir = repo_path / "guest-tools" / "image"
    cmd = ["./create-td-image.sh", "-v", version]
    if os.geteuid() != 0:
        cmd = ["sudo"] + cmd
    log(f"Building TD image via {image_dir} ({version})...")
    subprocess.run(cmd, check=True, cwd=image_dir, capture_output=True)
    return find_latest_td_image(image_dir, version)


def download_ubuntu_image(version: str = "24.04", dest_dir: str = IMAGE_DIR) -> str:
    """Download Ubuntu cloud image if not present."""
    os.makedirs(dest_dir, exist_ok=True)
    url = UBUNTU_CLOUD_IMAGES.get(version)
    if not url:
        raise ValueError(f"Unknown Ubuntu version: {version}. Available: {list(UBUNTU_CLOUD_IMAGES.keys())}")
    filename = f"ubuntu-{version}-cloudimg-amd64.img"
    dest_path = os.path.join(dest_dir, filename)
    if os.path.exists(dest_path):
        log(f"Image already exists: {dest_path}")
        return dest_path
    log(f"Downloading Ubuntu {version} cloud image...")
    log(f"URL: {url}")
    log(f"Destination: {dest_path}")

    def reporthook(count, block_size, total_size):
        percent = int(count * block_size * 100 / total_size)
        print(f"\rProgress: {percent}%", end="", flush=True, file=sys.stderr)

    urllib.request.urlretrieve(url, dest_path, reporthook)
    log("\nDownload complete!")
    if dest_path.endswith(".img"):
        qcow2_path = dest_path.replace(".img", ".qcow2")
        log(f"Converting to qcow2: {qcow2_path}")
        subprocess.run(
            ["qemu-img", "convert", "-f", "qcow2", "-O", "qcow2", dest_path, qcow2_path],
            check=True,
        )
        return qcow2_path
    return dest_path


def find_or_download_td_image(
    prefer_version: str = "24.04",
    *,
    build_from_tdx_repo: bool = False,
    tdx_repo_dir: str | None = None,
    tdx_repo_ref: str = "main",
) -> str:
    """Find existing TD image or download one."""
    if build_from_tdx_repo:
        repo_dir = tdx_repo_dir or DEFAULT_TDX_REPO_DIR
        return build_td_image_from_repo(repo_dir, prefer_version, ref=tdx_repo_ref)
    existing = find_existing_images()
    for img in existing:
        if "tdx" in img["name"].lower() or "td-guest" in img["name"].lower():
            log(f"Found TD image: {img['path']} ({img['size_gb']} GB)")
            return img["path"]
    for img in existing:
        if "cloud" in img["name"].lower() or "ubuntu" in img["name"].lower():
            log(f"Found cloud image: {img['path']} ({img['size_gb']} GB)")
            return img["path"]
    if existing:
        largest = max(existing, key=lambda x: x["size_gb"])
        log(f"Using existing image: {largest['path']} ({largest['size_gb']} GB)")
        return largest["path"]
    log("No existing images found. Downloading Ubuntu cloud image...")
    return download_ubuntu_image(prefer_version)


def find_td_image() -> str:
    """Find the TD guest image."""
    return find_or_download_td_image()


def indent_yaml(content: str, spaces: int) -> str:
    """Indent YAML content."""
    indent = " " * spaces
    return "\n".join(indent + line for line in content.split("\n"))


def build_extra_files_yaml(extra_files: list[dict[str, str]] | None) -> str:
    """Build cloud-init write_files YAML entries for extra files."""
    if not extra_files:
        return ""
    blocks = []
    for entry in extra_files:
        rel_path = entry.get("path")
        if not rel_path:
            continue
        rel_path = rel_path.lstrip("/")
        content = entry.get("content", "")
        permissions = entry.get("permissions", "0644")
        block = (
            f"  - path: /opt/workload/{rel_path}\n"
            f"    permissions: '{permissions}'\n"
            f"    content: |\n"
            f"{indent_yaml(content, 6)}\n"
        )
        blocks.append(block)
    return "\n".join(blocks)


def create_workload_image(
    base_image: str,
    docker_compose_content: str,
    port: int = 8080,
    enable_ssh: bool = False,
    extra_files: list[dict[str, str]] | None = None,
) -> tuple[str, str, str]:
    """Create a workload-specific image with docker-compose baked in."""
    workdir = tempfile.mkdtemp(prefix="ee-workload-")
    os.chmod(workdir, 0o755)
    workload_image = os.path.join(workdir, "workload.qcow2")
    subprocess.run(
        ["qemu-img", "create", "-f", "qcow2", "-b", base_image, "-F", "qcow2", workload_image],
        check=True,
        capture_output=True,
    )

    start_sh = load_template("start.sh").replace("{port}", str(port))
    get_quote = load_template("get-quote.py")
    network_config = load_template("network-config.yml")

    ssh_config = ""
    if enable_ssh:
        ssh_config = """
ssh_pwauth: false
"""

    extra_files_yaml = build_extra_files_yaml(extra_files)
    user_data = load_template("user-data.yml").format(
        ssh_config=ssh_config,
        docker_compose=indent_yaml(docker_compose_content, 6),
        start_sh=indent_yaml(start_sh, 6),
        get_quote=indent_yaml(get_quote, 6),
        extra_files=extra_files_yaml,
    )

    user_data_path = os.path.join(workdir, "user-data")
    meta_data_path = os.path.join(workdir, "meta-data")
    network_config_path = os.path.join(workdir, "network-config")

    with open(user_data_path, "w") as f:
        f.write(user_data)
    with open(meta_data_path, "w") as f:
        f.write("instance-id: ee-workload\nlocal-hostname: ee-workload\n")
    with open(network_config_path, "w") as f:
        f.write(network_config)

    cidata_iso = os.path.join(workdir, "cidata.iso")
    subprocess.run(
        ["genisoimage", "-output", cidata_iso, "-volid", "cidata", "-joliet", "-rock", user_data_path, meta_data_path, network_config_path],
        check=True,
        capture_output=True,
    )

    for fname in os.listdir(workdir):
        filepath = os.path.join(workdir, fname)
        if fname.endswith(".qcow2"):
            os.chmod(filepath, 0o666)
        else:
            os.chmod(filepath, 0o644)

    return workload_image, cidata_iso, workdir


def generate_tdx_domain_xml(name: str, disk_path: str, cidata_iso: str, memory_mb: int, vcpus: int) -> str:
    """Generate libvirt domain XML for TDX."""
    return f"""<domain type='kvm'>
  <name>{name}</name>
  <memory unit='MiB'>{memory_mb}</memory>
  <vcpu>{vcpus}</vcpu>
  <os>
    <type arch='x86_64' machine='q35'>hvm</type>
    <loader readonly='yes' type='pflash'>/usr/share/ovmf/OVMF.tdx.fd</loader>
  </os>
  <features>
    <acpi/>
    <apic/>
  </features>
  <cpu mode='host-passthrough'/>
  <clock offset='utc'/>
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2'/>
      <source file='{disk_path}'/>
      <target dev='vda' bus='virtio'/>
    </disk>
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='{cidata_iso}'/>
      <target dev='sda' bus='sata'/>
      <readonly/>
    </disk>
    <interface type='network'>
      <source network='default'/>
      <model type='virtio'/>
    </interface>
    <serial type='file'>
      <source path='/var/log/libvirt/qemu/{name}-serial.log'/>
      <target port='0'/>
    </serial>
    <console type='file'>
      <source path='/var/log/libvirt/qemu/{name}-serial.log'/>
      <target type='serial' port='0'/>
    </console>
  </devices>
  <launchSecurity type='tdx'>
    <policy>0x0000</policy>
  </launchSecurity>
</domain>"""


def start_td_vm(
    workload_image: str,
    cidata_iso: str,
    name: str = "ee-workload",
    memory_mb: int = 4096,
    vcpus: int = 2,
) -> str:
    """Start a TD VM and return the VM IP address."""
    vm_xml = generate_tdx_domain_xml(name, workload_image, cidata_iso, memory_mb, vcpus)
    fd, xml_path = tempfile.mkstemp(prefix=f"{name}-", suffix=".xml")
    with os.fdopen(fd, "w") as f:
        f.write(vm_xml)

    log(f"Cleaning up existing VM {name}...")
    subprocess.run(["sudo", "virsh", "destroy", name], capture_output=True)
    subprocess.run(["sudo", "virsh", "undefine", name, "--nvram"], capture_output=True)
    time.sleep(1)

    check = subprocess.run(["sudo", "virsh", "domstate", name], capture_output=True, text=True)
    if check.returncode == 0:
        log(f"Warning: VM {name} still exists, forcing undefine...")
        subprocess.run(["sudo", "virsh", "undefine", name, "--nvram", "--remove-all-storage"], capture_output=True)
        time.sleep(1)

    result = subprocess.run(["sudo", "virsh", "define", xml_path], capture_output=True, text=True)
    if result.returncode != 0:
        log(f"virsh define failed: {result.stderr}")
        raise RuntimeError(f"Failed to define VM: {result.stderr}")

    result = subprocess.run(["sudo", "virsh", "start", name], capture_output=True, text=True)
    if result.returncode != 0:
        log(f"virsh start failed: {result.stderr}")
        raise RuntimeError(f"Failed to start VM: {result.stderr}")

    log(f"VM {name} started successfully")
    time.sleep(10)

    result = subprocess.run(["sudo", "virsh", "domstate", name], capture_output=True, text=True)
    log(f"VM state: {result.stdout.strip()}")

    ip = None
    for _ in range(60):
        result = subprocess.run(["sudo", "virsh", "domifaddr", name, "--source", "lease"], capture_output=True, text=True)
        for line in result.stdout.splitlines():
            if "ipv4" in line:
                ip = line.split()[3].split("/")[0]
                break
        if ip:
            break
        time.sleep(5)
    if not ip:
        raise RuntimeError(f"Failed to determine IP for {name}")
    return ip


def get_public_ip() -> str:
    """Get the host's public IP address."""
    methods = [
        lambda: subprocess.run(
            ["hostname", "-I"],
            capture_output=True, text=True, timeout=5
        ).stdout.split()[0] if not subprocess.run(
            ["hostname", "-I"], capture_output=True, text=True, timeout=5
        ).stdout.split()[0].startswith(("192.168.", "10.", "172.")) else None,
        lambda: urllib.request.urlopen("https://ifconfig.me", timeout=5).read().decode().strip(),
        lambda: urllib.request.urlopen("https://api.ipify.org", timeout=5).read().decode().strip(),
    ]
    for method in methods:
        try:
            ip = method()
            if ip and not ip.startswith(("192.168.", "10.", "172.16.", "172.17.", "172.18.")):
                return ip
        except Exception:
            continue
    try:
        result = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5)
        for ip in result.stdout.split():
            if not ip.startswith(("192.168.", "10.", "172.", "127.")):
                return ip
    except Exception:
        pass
    return ""


def setup_port_forward(vm_ip: str, vm_port: int, host_port: int | None = None) -> int:
    """Set up iptables port forwarding from host to VM."""
    host_port = host_port or vm_port

    def remove_nat_rules(chain: str) -> None:
        result = subprocess.run(
            ["sudo", "iptables", "-t", "nat", "-L", chain, "--line-numbers"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return
        rule_numbers = []
        for line in result.stdout.splitlines():
            parts = line.split()
            if not parts or not parts[0].isdigit():
                continue
            if (
                f"dpt:{host_port}" in line
                and "DNAT" in line
                and f"to:{vm_ip}:{vm_port}" in line
            ):
                rule_numbers.append(int(parts[0]))
        for number in reversed(rule_numbers):
            subprocess.run(["sudo", "iptables", "-t", "nat", "-D", chain, str(number)], capture_output=True)

    def remove_forward_rules() -> None:
        result = subprocess.run(["sudo", "iptables", "-L", "FORWARD", "--line-numbers"], capture_output=True, text=True)
        if result.returncode != 0:
            return
        rule_numbers = []
        for line in result.stdout.splitlines():
            parts = line.split()
            if not parts or not parts[0].isdigit():
                continue
            if f"dpt:{vm_port}" in line and vm_ip in line and "ACCEPT" in line:
                rule_numbers.append(int(parts[0]))
        for number in reversed(rule_numbers):
            subprocess.run(["sudo", "iptables", "-D", "FORWARD", str(number)], capture_output=True)

    remove_nat_rules("PREROUTING")
    remove_nat_rules("OUTPUT")
    remove_forward_rules()

    result = subprocess.run(
        ["sudo", "iptables", "-t", "nat", "-A", "PREROUTING", "-p", "tcp", "--dport", str(host_port),
         "-j", "DNAT", "--to-destination", f"{vm_ip}:{vm_port}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to add PREROUTING rule: {result.stderr}")

    result = subprocess.run(
        ["sudo", "iptables", "-t", "nat", "-A", "OUTPUT", "-p", "tcp", "-d", "127.0.0.1",
         "--dport", str(host_port), "-j", "DNAT", "--to-destination", f"{vm_ip}:{vm_port}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log(f"Warning: Failed to add OUTPUT rule: {result.stderr}")

    result = subprocess.run(
        ["sudo", "iptables", "-A", "FORWARD", "-p", "tcp", "-d", vm_ip, "--dport", str(vm_port), "-j", "ACCEPT"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log(f"Warning: Failed to add FORWARD rule: {result.stderr}")

    log(f"Port forwarding configured: *:{host_port} -> {vm_ip}:{vm_port}")
    return host_port


def wait_for_ready(ip: str, port: int = 8080, timeout: int = 300) -> None:
    """Wait for workload to be ready by checking port."""
    start = time.time()
    last_print = 0
    while time.time() - start < timeout:
        elapsed = int(time.time() - start)
        if elapsed - last_print >= 30:
            last_print = elapsed
            log(f"Waiting for port {port}... ({elapsed}s elapsed)")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((ip, port))
            sock.close()
            if result == 0:
                log(f"Port {port} is open on {ip}")
                time.sleep(2)
                return
        except Exception:
            pass
        time.sleep(5)
    raise TimeoutError(f"Port {port} not ready within {timeout}s")


def get_quote_from_vm(ip: str, port: int = 8080) -> str:
    """Retrieve quote from VM via HTTP. Returns base64-encoded quote."""
    url = f"http://{ip}:{port}/quote.json"
    log(f"Fetching quote from {url}")
    response = urllib.request.urlopen(url, timeout=30)
    data = json.loads(response.read().decode())
    if not data.get("success"):
        raise RuntimeError(f"Quote generation failed in VM: {data.get('error', 'unknown')}")
    if not data.get("quote"):
        raise RuntimeError("No quote in VM response")
    return data["quote"]


def create_td_vm(
    docker_compose_path: str,
    name: str = "ee-workload",
    port: int = 8080,
    enable_ssh: bool = False,
    extra_files: list[dict[str, str]] | None = None,
    base_image: str | None = None,
) -> dict:
    """Create a TD VM with the given workload."""
    log("Checking requirements...")
    check_requirements()

    log("Finding TD base image...")
    if base_image:
        base_image = os.path.expanduser(base_image)
        if not os.path.exists(base_image):
            raise FileNotFoundError(f"Base image not found: {base_image}")
    else:
        base_image = find_td_image()
    log(f"Using base image: {base_image}")

    log(f"Reading docker-compose from {docker_compose_path}...")
    with open(docker_compose_path) as f:
        docker_compose_content = f.read()

    log("Creating workload image...")
    workload_image, cidata_iso, workdir = create_workload_image(
        base_image,
        docker_compose_content,
        port=port,
        enable_ssh=enable_ssh,
        extra_files=extra_files,
    )

    log("Starting TD VM...")
    ip = start_td_vm(workload_image, cidata_iso, name)
    log(f"VM IP: {ip}")

    log("Waiting for workload...")
    wait_for_ready(ip, port=port, timeout=300)

    log("Retrieving quote...")
    quote = get_quote_from_vm(ip, port=port)

    return {
        "name": name,
        "ip": ip,
        "port": port,
        "quote": quote,
        "workdir": workdir,
    }


def cleanup_td_vms(prefixes: Sequence[str] | None = None) -> None:
    """Destroy any TD VMs whose names match the provided prefixes."""
    if prefixes is None:
        prefixes = ("ee-deploy-", "ee-workload", "ee-")
    elif isinstance(prefixes, str):
        prefixes = (prefixes,)
    else:
        prefixes = tuple(prefixes)
    result = subprocess.run(["sudo", "virsh", "list", "--all", "--name"], capture_output=True, text=True)
    if result.returncode != 0:
        log(f"Warning: failed to list VMs: {result.stderr.strip()}")
        return
    for name in [line.strip() for line in result.stdout.splitlines() if line.strip()]:
        if not name.startswith(prefixes):
            continue
        log(f"Cleaning up existing VM {name}...")
        subprocess.run(["sudo", "virsh", "destroy", name], capture_output=True)
        subprocess.run(["sudo", "virsh", "undefine", name, "--nvram", "--remove-all-storage"], capture_output=True)


def cleanup_deploy_releases(repo: str, token: str, prefix: str = "deploy-") -> None:
    """Delete existing deploy releases so only one remains."""
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/releases?per_page=100",
            headers=headers,
        )
        with urllib.request.urlopen(req) as response:
            releases = json.loads(response.read().decode())
        for release in releases:
            tag = release.get("tag_name", "")
            release_id = release.get("id")
            if not tag.startswith(prefix) or not release_id:
                continue
            delete_req = urllib.request.Request(
                f"https://api.github.com/repos/{repo}/releases/{release_id}",
                headers=headers,
                method="DELETE",
            )
            try:
                with urllib.request.urlopen(delete_req):
                    pass
            except Exception as exc:
                log(f"Warning: failed to delete release {tag}: {exc}")
    except Exception as exc:
        log(f"Warning: release cleanup failed: {exc}")


def create_release(
    quote: str,
    endpoint: str,
    repo: str | None = None,
    token: str | None = None,
    seal_vm: bool = False,
) -> str:
    """Create a GitHub release with attestation data."""
    repo = repo or os.environ.get("GITHUB_REPOSITORY")
    token = token or os.environ.get("GITHUB_TOKEN")

    if not repo or not token:
        raise ValueError("GITHUB_REPOSITORY and GITHUB_TOKEN must be set")

    cleanup_deploy_releases(repo, token)

    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    tag = f"deploy-{timestamp}"

    attestation = {
        "version": "1.0",
        "quote": quote,
        "endpoint": endpoint,
        "timestamp": now.isoformat().replace("+00:00", "Z"),
        "repo": repo,
        "seal_vm": seal_vm,
    }

    body = f"""# Easy Enclave Deployment

**Endpoint**: {endpoint}
**Sealed**: {str(seal_vm).lower()}

## Attestation

```json
{json.dumps(attestation, indent=2)}
```

## Usage

```python
from easyenclave import connect
client = connect("{repo}")
```
"""

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {
        "tag_name": tag,
        "name": f"Deployment {timestamp}",
        "body": body,
        "draft": False,
        "prerelease": False,
    }
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases",
        data=json.dumps(payload).encode(),
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as response:
        release_data = json.loads(response.read().decode())
    upload_url = release_data.get("upload_url", "").split("{", 1)[0]
    if not upload_url:
        raise RuntimeError("Release upload URL missing")

    attestation_bytes = json.dumps(attestation, indent=2).encode()
    upload_req = urllib.request.Request(
        f"{upload_url}?name=attestation.json",
        data=attestation_bytes,
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(upload_req):
        log("Uploaded attestation.json")

    return release_data.get("html_url") or f"https://github.com/{repo}/releases/tag/{tag}"

EE_CONTROL_WS = os.getenv("EE_CONTROL_WS", "")
EE_REPO = os.getenv("EE_REPO", "")
EE_RELEASE_TAG = os.getenv("EE_RELEASE_TAG", "")
EE_APP_NAME = os.getenv("EE_APP_NAME", "")
EE_NETWORK = os.getenv("EE_NETWORK", "forge-1")
EE_AGENT_ID = os.getenv("EE_AGENT_ID", str(uuid.uuid4()))
EE_BACKEND_URL = os.getenv("EE_BACKEND_URL", "http://127.0.0.1:8080")
EE_HEALTH_INTERVAL_SEC = int(os.getenv("EE_HEALTH_INTERVAL_SEC", "60"))
EE_RECONNECT_DELAY_SEC = int(os.getenv("EE_RECONNECT_DELAY_SEC", "5"))
EE_RATLS_ENABLED = env_bool("EE_RATLS_ENABLED", True)
EE_RATLS_CERT_TTL_SEC = int(os.getenv("EE_RATLS_CERT_TTL_SEC", "3600"))
EE_RATLS_SKIP_PCCS = env_bool("EE_RATLS_SKIP_PCCS", False)
EE_CONTROL_ALLOWLIST_PATH = os.getenv("EE_CONTROL_ALLOWLIST_PATH", "")
EE_CONTROL_ALLOWLIST_REPO = os.getenv("EE_CONTROL_ALLOWLIST_REPO", "")
EE_CONTROL_ALLOWLIST_TAG = os.getenv("EE_CONTROL_ALLOWLIST_TAG", "")
EE_CONTROL_ALLOWLIST_ASSET = os.getenv("EE_CONTROL_ALLOWLIST_ASSET", "agent-attestation-allowlist.json")
EE_CONTROL_ALLOWLIST_REQUIRED = env_bool("EE_CONTROL_ALLOWLIST_REQUIRED", True)
EE_CONTROL_ALLOWLIST_TOKEN = os.getenv("EE_CONTROL_ALLOWLIST_TOKEN", "")


@dataclass
class Deployment:
    """Deployment state."""

    id: str
    repo: str
    port: int
    status: str  # pending, cloning, deploying, complete, failed
    cleanup_prefixes: Optional[list[str]] = None
    bundle_artifact_id: Optional[int] = None
    bundle_b64: Optional[str] = None
    bundle_format: Optional[str] = None
    private_env: Optional[str] = None
    seal_vm: bool = False
    vm_name: Optional[str] = None
    vm_ip: Optional[str] = None
    quote: Optional[str] = None
    release_url: str = ""
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
        data.pop("bundle_b64", None)
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


@dataclass
class RatlsMaterial:
    cert_path: Path
    key_path: Path


RATLS_MATERIAL: RatlsMaterial | None = None


def ensure_ratls_material(common_name: str = "easyenclave-agent") -> RatlsMaterial:
    global RATLS_MATERIAL
    if RATLS_MATERIAL:
        return RATLS_MATERIAL

    ratls_dir = Path("/var/lib/easy-enclave/ratls")
    ratls_dir.mkdir(parents=True, exist_ok=True)
    cert_path = ratls_dir / "ratls.crt"
    key_path = ratls_dir / "ratls.key"

    key = ec.generate_private_key(ec.SECP256R1())
    report_data = report_data_for_pubkey(key.public_key())
    quote = get_tdx_quote(report_data)
    cert_pem = build_ratls_cert(quote, key, common_name=common_name, ttl_seconds=EE_RATLS_CERT_TTL_SEC)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )

    cert_path.write_bytes(cert_pem)
    key_path.write_bytes(key_pem)
    os.chmod(cert_path, 0o600)
    os.chmod(key_path, 0o600)

    RATLS_MATERIAL = RatlsMaterial(cert_path=cert_path, key_path=key_path)
    return RATLS_MATERIAL


def build_ratls_server_context(material: RatlsMaterial) -> ssl.SSLContext:
    context = ssl.create_default_context(purpose=ssl.Purpose.CLIENT_AUTH)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.load_cert_chain(certfile=str(material.cert_path), keyfile=str(material.key_path))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def build_ratls_client_context(material: RatlsMaterial) -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.load_cert_chain(certfile=str(material.cert_path), keyfile=str(material.key_path))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def load_control_allowlist() -> Optional[dict]:
    if EE_CONTROL_ALLOWLIST_PATH:
        return json.loads(Path(EE_CONTROL_ALLOWLIST_PATH).read_text(encoding="utf-8"))
    if not EE_CONTROL_ALLOWLIST_REPO or not EE_CONTROL_ALLOWLIST_TAG:
        return None

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "easy-enclave-agent",
    }
    if EE_CONTROL_ALLOWLIST_TOKEN:
        headers["Authorization"] = f"Bearer {EE_CONTROL_ALLOWLIST_TOKEN}"

    release_url = f"https://api.github.com/repos/{EE_CONTROL_ALLOWLIST_REPO}/releases/tags/{EE_CONTROL_ALLOWLIST_TAG}"
    req = Request(release_url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as response:
        release = json.loads(response.read().decode())

    asset_url = None
    for asset in release.get("assets", []):
        if asset.get("name") == EE_CONTROL_ALLOWLIST_ASSET:
            asset_url = asset.get("browser_download_url")
            break
    if not asset_url:
        raise RuntimeError(f"Control allowlist asset not found: {EE_CONTROL_ALLOWLIST_ASSET}")

    req = Request(asset_url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode())


def extract_peer_cert(ws) -> bytes:
    transport = getattr(getattr(ws, "_response", None), "connection", None)
    if transport:
        transport = transport.transport
    else:
        transport = getattr(ws, "_transport", None)
    if not transport:
        return b""
    ssl_obj = transport.get_extra_info("ssl_object")
    if not ssl_obj:
        return b""
    return ssl_obj.getpeercert(binary_form=True) or b""


def verify_control_plane_ratls(ws) -> None:
    cert_der = extract_peer_cert(ws)
    allowlist = load_control_allowlist()
    result = verify_ratls_cert(
        cert_der,
        allowlist,
        pccs_url=os.getenv("EE_RATLS_PCCS_URL") or None,
        skip_pccs=EE_RATLS_SKIP_PCCS,
        require_allowlist=EE_CONTROL_ALLOWLIST_REQUIRED,
    )
    if not result.verified:
        raise RuntimeError(f"control_plane_ratls_failed:{result.reason}")


def write_bundle_files(bundle_dir: str, compose_path: str, extra_files: list[dict[str, str]]) -> str:
    """Write bundle files to /opt/workload and return compose path."""

    target_root = Path("/opt/workload")
    target_root.mkdir(parents=True, exist_ok=True)
    bundle_root = Path(bundle_dir).resolve()
    src_compose = Path(compose_path).resolve()
    if bundle_root not in src_compose.parents:
        raise ValueError(f"Compose path {src_compose} is not under bundle root {bundle_root}")
    compose_rel = src_compose.relative_to(bundle_root)
    target_compose = target_root / compose_rel
    target_compose.parent.mkdir(parents=True, exist_ok=True)
    target_compose.write_text(src_compose.read_text(encoding="utf-8"), encoding="utf-8")

    for entry in extra_files:
        rel_path = entry.get("path")
        if not rel_path:
            continue
        dest_path = target_root / rel_path.lstrip("/")
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_text(entry.get("content", ""), encoding="utf-8")
        if entry.get("permissions"):
            os.chmod(dest_path, int(entry["permissions"], 8))
    return str(target_compose)


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
    compose_dir = str(Path(compose_path).parent)
    result = subprocess.run(
        [*compose_cmd, "-f", compose_path, "up", "-d", "--remove-orphans"],
        capture_output=True,
        text=True,
        cwd=compose_dir,
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


def safe_extract_tar(archive: tarfile.TarFile, dest: str) -> None:
    dest_path = os.path.abspath(dest)
    for member in archive.getmembers():
        member_path = os.path.abspath(os.path.join(dest, member.name))
        if os.path.commonpath([dest_path, member_path]) != dest_path:
            raise RuntimeError("Bundle archive contains unsafe path")
    archive.extractall(dest)


def safe_extract_zip(archive: zipfile.ZipFile, dest: str) -> None:
    dest_path = os.path.abspath(dest)
    for member in archive.infolist():
        member_path = os.path.abspath(os.path.join(dest, member.filename))
        if os.path.commonpath([dest_path, member_path]) != dest_path:
            raise RuntimeError("Bundle archive contains unsafe path")
    archive.extractall(dest)


def materialize_inline_bundle(bundle_b64: str, bundle_format: Optional[str]) -> str:
    """Decode an inline bundle into a temp directory."""
    tmpdir = tempfile.mkdtemp(prefix="ee-bundle-")
    archive_format = (bundle_format or "tar.gz").lower()
    archive_bytes = base64.b64decode(bundle_b64.encode("ascii"))

    if archive_format in {"tar.gz", "tgz"}:
        archive_path = os.path.join(tmpdir, "bundle.tar.gz")
        with open(archive_path, "wb") as f:
            f.write(archive_bytes)
        with tarfile.open(archive_path, "r:gz") as tar:
            safe_extract_tar(tar, tmpdir)
        return tmpdir
    if archive_format == "zip":
        archive_path = os.path.join(tmpdir, "bundle.zip")
        with open(archive_path, "wb") as f:
            f.write(archive_bytes)
        with zipfile.ZipFile(archive_path) as zf:
            safe_extract_zip(zf, tmpdir)
        return tmpdir

    raise ValueError(f"Unsupported bundle_format: {archive_format}")


def load_bundle(bundle_dir: str) -> tuple[str, list[dict[str, str]], dict]:
    """Load docker-compose and extra files from the bundle."""

    root = Path(bundle_dir)
    compose_paths = list(root.rglob("docker-compose.yml")) + list(root.rglob("docker-compose.yaml"))
    if not compose_paths:
        raise FileNotFoundError("Bundle missing docker-compose.yml")
    compose_root = root / "docker-compose.yml"
    if not compose_root.exists():
        compose_root = root / "docker-compose.yaml"
    if compose_root.exists():
        compose_path = compose_root
    elif len(compose_paths) == 1:
        compose_path = compose_paths[0]
    else:
        raise ValueError("Bundle has multiple docker-compose files and no root compose")

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

        if deployment.bundle_b64:
            log("Using inline bundle payload...")
            bundle_dir = materialize_inline_bundle(deployment.bundle_b64, deployment.bundle_format)
        elif deployment.bundle_artifact_id is not None:
            log(f"Downloading bundle artifact {deployment.bundle_artifact_id} for {deployment.repo}...")
            bundle_dir = download_bundle_artifact(deployment.repo, deployment.bundle_artifact_id, token)
        else:
            raise RuntimeError("bundle_artifact_id or bundle_b64 is required for deployment")

        compose_path, extra_files, bundle_meta = load_bundle(bundle_dir)

        if deployment.cleanup_prefixes:
            log("cleanup_prefixes ignored in single-VM mode")
        if deployment.vm_name:
            log("vm_name ignored in single-VM mode")

        compose_path = write_bundle_files(bundle_dir, compose_path, extra_files)
        compose_dir = Path(compose_path).parent
        env_public_path = None
        if bundle_meta.get("env_public"):
            env_public_path = compose_dir / ".env.public"
            env_public_path.write_text(bundle_meta["env_public"], encoding="utf-8")
        env_private_path = None
        if deployment.private_env:
            env_private_path = compose_dir / ".env.private"
            env_private_path.write_text(deployment.private_env, encoding="utf-8")
            os.chmod(env_private_path, 0o600)
        env_path = compose_dir / ".env"
        parts = []
        if env_public_path:
            parts.append(env_public_path)
        if env_private_path:
            parts.append(env_private_path)
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
            try:
                shutil.chown("/home/ubuntu/.ssh", user="ubuntu", group="ubuntu")
                shutil.chown("/home/ubuntu/.ssh/authorized_keys", user="ubuntu", group="ubuntu")
            except Exception as exc:
                log(f"Warning: failed to chown authorized_keys: {exc}")

        run_docker_compose(compose_path)

        attestation = build_attestation()
        deployment.quote = attestation["quote"]
        public_ip = get_public_ip()
        if not public_ip:
            log("Warning: unable to determine public IP; using localhost endpoint")
        deployment.vm_ip = public_ip
        endpoint = f"http://{public_ip or '127.0.0.1'}:{deployment.port}"
        try:
            release_url = create_release(
                deployment.quote,
                endpoint,
                repo=deployment.repo,
                token=token,
                seal_vm=deployment.seal_vm,
            )
            deployment.release_url = release_url
        except Exception as exc:
            deployment.release_url = ""
            log(f"Warning: release creation failed: {exc}")
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
    bundle_b64 = data.get("bundle_b64") or None
    bundle_format = data.get("bundle_format")
    if bundle_b64 is not None:
        if not isinstance(bundle_b64, str):
            return web.json_response({"error": "bundle_b64 must be a string"}, status=400)
        if bundle_format is not None and not isinstance(bundle_format, str):
            return web.json_response({"error": "bundle_format must be a string"}, status=400)
        bundle_artifact_id = None
    else:
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
        bundle_b64=bundle_b64,
        bundle_format=bundle_format,
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

    control_ws = EE_CONTROL_WS
    ssl_context = None
    if EE_RATLS_ENABLED:
        if control_ws.startswith("ws://"):
            control_ws = "wss://" + control_ws[len("ws://"):]
        if not control_ws.startswith("wss://"):
            log("EE_CONTROL_WS must be wss:// when EE_RATLS_ENABLED=true")
            return
        ssl_context = build_ratls_client_context(ensure_ratls_material())

    while True:
        try:
            async with ClientSession() as session:
                async with session.ws_connect(control_ws, ssl=ssl_context) as ws:
                    if EE_RATLS_ENABLED:
                        verify_control_plane_ratls(ws)
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
    ssl_context = None
    if EE_RATLS_ENABLED:
        ssl_context = build_ratls_server_context(ensure_ratls_material())
    web.run_app(app, host=args.host, port=args.port, ssl_context=ssl_context)


if __name__ == "__main__":
    main()
