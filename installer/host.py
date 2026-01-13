#!/usr/bin/env python3
"""
TD VM management using Canonical TDX tooling.

Uses tdvirsh for VM management and trustauthority-cli for quote generation.
See: https://github.com/canonical/tdx
"""

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

# Force unbuffered output for real-time logging
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)


def log(msg):
    """Print to stderr for logging (keeps stdout clean for JSON output)."""
    print(msg, file=sys.stderr)


# Templates directory - check multiple locations
_script_dir = Path(__file__).parent
_possible_template_dirs = [
    _script_dir / "templates",             # installer/host.py -> installer/templates
    _script_dir.parent / "templates",      # legacy layout
]
TEMPLATES_DIR = next((d for d in _possible_template_dirs if d.exists()), _possible_template_dirs[0])


def load_template(name: str) -> str:
    """Load a template file from the templates directory."""
    return (TEMPLATES_DIR / name).read_text()


def sha256_file(path: str) -> str:
    """Compute sha256 for a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# Default paths (Canonical TDX layout)
TDX_REPO_URL = "https://github.com/canonical/tdx.git"
TDX_TOOLS_DIR = "/opt/tdx"
DEFAULT_TDX_REPO_DIR = "/var/lib/easy-enclave/tdx"
DEFAULT_TDX_GUEST_VERSION = "24.04"
IMAGE_DIR = "/var/lib/easy-enclave"
DEFAULT_TD_IMAGE = f"{IMAGE_DIR}/td-guest.qcow2"

# Ubuntu cloud image URLs (TDX-compatible)
UBUNTU_CLOUD_IMAGES = {
    "24.04": "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img",
    "24.10": "https://cloud-images.ubuntu.com/oracular/current/oracular-server-cloudimg-amd64.img",
}

# Deployment state directory
DEPLOYMENTS_DIR = Path("/var/lib/easy-enclave/deployments")
PCCS_ENV_VARS = (
    "PCCS_URL",
    "EE_PCCS_URL",
    "TDX_PCCS_URL",
    "COLLATERAL_SERVICE_URL",
    "EE_COLLATERAL_SERVICE_URL",
)
PCCS_CONFIG_PATHS = (
    "/etc/sgx_default_qcnl.conf",
    "/etc/qcnl.conf",
    "/etc/tdx-qgs/qgs.conf",
)
PCCS_CONFIG_KEYS = ("pccs_url", "collateral_service_url")


def _parse_key_value_file(path: str) -> dict[str, str]:
    """Parse simple KEY=VALUE config files."""
    values: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                match = re.match(r"([A-Za-z_]+)\s*=\s*(.+)", line)
                if not match:
                    continue
                key = match.group(1).strip().lower()
                value = match.group(2).strip().strip('"').strip("'")
                if value:
                    values[key] = value
    except OSError:
        return {}
    return values


def _parse_systemd_env(service: str) -> tuple[dict[str, str], list[str]]:
    """Read Environment and EnvironmentFile entries from systemd."""
    if not shutil.which("systemctl"):
        return {}, []
    result = subprocess.run(
        ["systemctl", "show", "-p", "Environment", "-p", "EnvironmentFile", service],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {}, []

    env_values: dict[str, str] = {}
    env_files: list[str] = []
    for line in result.stdout.splitlines():
        if line.startswith("Environment="):
            raw = line.split("=", 1)[1].strip()
            if raw:
                for token in shlex.split(raw):
                    if "=" in token:
                        key, value = token.split("=", 1)
                        if value:
                            env_values[key.strip().lower()] = value.strip()
        elif line.startswith("EnvironmentFile="):
            raw = line.split("=", 1)[1].strip()
            if raw:
                for token in shlex.split(raw):
                    path = token.lstrip("-")
                    if path:
                        env_files.append(path)
    return env_values, env_files


def _find_collateral_url() -> tuple[str, str]:
    """Return collateral service URL and source."""
    for key in PCCS_ENV_VARS:
        value = os.environ.get(key, "").strip()
        if value:
            return value, f"env:{key}"

    env_values, env_files = _parse_systemd_env("qgsd")
    for key in PCCS_CONFIG_KEYS:
        if key in env_values and env_values[key]:
            return env_values[key], "systemd:qgsd"

    for env_file in env_files:
        values = _parse_key_value_file(env_file)
        for key in PCCS_CONFIG_KEYS:
            if key in values and values[key]:
                return values[key], env_file

    for path in PCCS_CONFIG_PATHS:
        if not os.path.exists(path):
            continue
        values = _parse_key_value_file(path)
        for key in PCCS_CONFIG_KEYS:
            if key in values and values[key]:
                return values[key], path
    return "", ""


def _check_qgs() -> None:
    """Ensure QGS is running."""
    if shutil.which("systemctl"):
        result = subprocess.run(["systemctl", "is-active", "--quiet", "qgsd"])
        if result.returncode != 0:
            raise RuntimeError(
                "QGS not running (qgsd inactive). See installer/README.md for host setup."
            )
    else:
        result = subprocess.run(["pgrep", "-x", "qgsd"], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                "QGS not running (qgsd not found). See installer/README.md for host setup."
            )

    log("QGS: running")


def check_requirements() -> None:
    """Check that TDX and libvirt are available. Fails fast if not."""
    # Check kernel
    result = subprocess.run(['uname', '-r'], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("Cannot get kernel version")
    kernel = result.stdout.strip()
    log(f"Kernel: {kernel}")

    # Check TDX support
    tdx_enabled = False
    tdx_path = "/sys/module/kvm_intel/parameters/tdx"
    if os.path.exists(tdx_path):
        with open(tdx_path) as f:
            if f.read().strip() in ('Y', '1'):
                tdx_enabled = True
    if not tdx_enabled:
        raise RuntimeError(f"TDX not enabled (check {tdx_path})")
    log("TDX: enabled")

    _check_qgs()

    # Check libvirt
    result = subprocess.run(['virsh', 'version'], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("libvirt not available (virsh not found)")
    log("libvirt: available")

    # Check libvirt TDX support
    result = subprocess.run(['virsh', 'domcapabilities', '--machine', 'q35'], capture_output=True, text=True)
    if 'tdx' not in result.stdout.lower():
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
    candidates = sorted(
        image_dir.glob(f"tdx-guest-ubuntu-{version}-*.qcow2"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        existing = str(candidates[0])
        log(f"Found existing TD image: {existing}")
        return existing

    cmd = ["./create-td-image.sh", "-v", version]
    if os.geteuid() != 0:
        cmd = ["sudo"] + cmd
    log(f"Building TD image via {image_dir} ({version})...")
    try:
        subprocess.run(cmd, check=True, cwd=image_dir, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        if exc.stdout:
            log(f"create-td-image.sh stdout:\n{exc.stdout.rstrip()}")
        if exc.stderr:
            log(f"create-td-image.sh stderr:\n{exc.stderr.rstrip()}")
        raise
    return find_latest_td_image(image_dir, version)


def download_ubuntu_image(version: str = "24.04", dest_dir: str = IMAGE_DIR) -> str:
    """
    Download Ubuntu cloud image if not present.

    Returns path to the downloaded image.
    """
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

    # Download with progress
    def reporthook(count, block_size, total_size):
        percent = int(count * block_size * 100 / total_size)
        print(f"\rProgress: {percent}%", end='', flush=True, file=sys.stderr)

    urllib.request.urlretrieve(url, dest_path, reporthook)
    log("\nDownload complete!")

    # Convert to qcow2 if needed
    if dest_path.endswith('.img'):
        qcow2_path = dest_path.replace('.img', '.qcow2')
        log(f"Converting to qcow2: {qcow2_path}")
        subprocess.run([
            'qemu-img', 'convert', '-f', 'qcow2', '-O', 'qcow2',
            dest_path, qcow2_path
        ], check=True)
        return qcow2_path

    return dest_path


def find_or_download_td_image(
    prefer_version: str = "24.04",
    *,
    build_from_tdx_repo: bool = False,
    tdx_repo_dir: str | None = None,
    tdx_repo_ref: str = "main",
) -> str:
    """
    Find existing TD image or download one.

    Returns path to usable image.
    """
    # Optionally build via canonical/tdx.
    if build_from_tdx_repo:
        repo_dir = tdx_repo_dir or DEFAULT_TDX_REPO_DIR
        return build_td_image_from_repo(repo_dir, prefer_version, ref=tdx_repo_ref)

    # First, look for existing images
    existing = find_existing_images()

    # Prefer images with 'tdx' or 'td-guest' in name
    for img in existing:
        if 'tdx' in img['name'].lower() or 'td-guest' in img['name'].lower():
            log(f"Found TD image: {img['path']} ({img['size_gb']} GB)")
            return img['path']

    # Then look for any cloud image
    for img in existing:
        if 'cloud' in img['name'].lower() or 'ubuntu' in img['name'].lower():
            log(f"Found cloud image: {img['path']} ({img['size_gb']} GB)")
            return img['path']

    # If any qcow2/img exists, use the largest one
    if existing:
        largest = max(existing, key=lambda x: x['size_gb'])
        log(f"Using existing image: {largest['path']} ({largest['size_gb']} GB)")
        return largest['path']

    # No images found - download
    log("No existing images found. Downloading Ubuntu cloud image...")
    return download_ubuntu_image(prefer_version)


def find_td_image() -> str:
    """Find the TD guest image (legacy function, now uses find_or_download)."""
    return find_or_download_td_image()


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
) -> str:
    """
    Create a workload-specific image with docker-compose baked in.

    Returns path to the new image.
    """
    workdir = tempfile.mkdtemp(prefix="ee-workload-")
    # Make workdir world-readable so libvirt/QEMU can access it
    os.chmod(workdir, 0o755)
    workload_image = os.path.join(workdir, "workload.qcow2")

    # Create overlay image (don't specify size - inherit from base)
    subprocess.run([
        'qemu-img', 'create', '-f', 'qcow2',
        '-b', base_image, '-F', 'qcow2',
        workload_image
    ], check=True, capture_output=True)

    # Load templates
    start_sh = load_template("start.sh").replace("{port}", str(port))
    get_quote = load_template("get-quote.py")
    network_config = load_template("network-config.yml")

    # SSH config (off by default)
    ssh_config = ""
    if enable_ssh:
        ssh_config = """
ssh_pwauth: false
"""

    # Build user-data from template
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

    with open(user_data_path, 'w') as f:
        f.write(user_data)
    with open(meta_data_path, 'w') as f:
        f.write("instance-id: ee-workload\nlocal-hostname: ee-workload\n")
    with open(network_config_path, 'w') as f:
        f.write(network_config)

    # Create cloud-init ISO
    cidata_iso = os.path.join(workdir, "cidata.iso")
    subprocess.run([
        'genisoimage', '-output', cidata_iso,
        '-volid', 'cidata', '-joliet', '-rock',
        user_data_path, meta_data_path, network_config_path
    ], check=True, capture_output=True)

    # Make all files accessible by libvirt/QEMU (qcow2 needs write access)
    for f in os.listdir(workdir):
        filepath = os.path.join(workdir, f)
        if f.endswith('.qcow2'):
            os.chmod(filepath, 0o666)  # VM disk needs write access
        else:
            os.chmod(filepath, 0o644)

    return workload_image, cidata_iso, workdir


def build_vm_image_id(tag: str, sha256: str) -> str:
    """Build vm_image_id contents."""
    lines = []
    if tag:
        lines.append(f"tag={tag}")
    if sha256:
        lines.append(f"sha256={sha256}")
    return "\n".join(lines) + ("\n" if lines else "")


def build_vm_image_id_yaml(tag: str, sha256: str) -> str:
    """Build cloud-init write_files entry for vm_image_id."""
    content = build_vm_image_id(tag, sha256)
    if not content:
        return ""
    return (
        "  - path: /etc/easy-enclave/vm_image_id\n"
        "    permissions: '0644'\n"
        "    content: |\n"
        f"{indent_yaml(content, 6)}\n"
    )


def build_agent_env(base_env: dict[str, str], extra_env: dict[str, str] | None = None) -> str:
    lines: list[str] = []
    if extra_env:
        for key, value in sorted(extra_env.items()):
            if value is None or value == "":
                continue
            lines.append(f"{key}={value}")
    for key, value in base_env.items():
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def create_agent_image(
    base_image: str,
    agent_py: str,
    agent_verify_py: str,
    agent_ratls_py: str,
    vm_image_tag: str,
    vm_image_sha256: str,
    agent_port: int = 8000,
    agent_env: str = "",
    nginx_conf: str = "",
    control_plane_files: dict[str, str] | None = None,
    sdk_files: dict[str, str] | None = None,
    user_data_template: str = "agent-user-data.yml",
) -> str:
    """Create an agent VM image with agent service installed."""
    workdir = tempfile.mkdtemp(prefix="ee-agent-")
    os.chmod(workdir, 0o755)
    agent_image = os.path.join(workdir, "agent.qcow2")

    subprocess.run([
        'qemu-img', 'create', '-f', 'qcow2',
        '-b', base_image, '-F', 'qcow2',
        agent_image
    ], check=True, capture_output=True)

    agent_service = load_template("agent-service.service").format(agent_port=agent_port)
    network_config = load_template("network-config.yml")
    vm_image_id = build_vm_image_id_yaml(vm_image_tag, vm_image_sha256)
    control_plane_files = control_plane_files or {}
    sdk_files = sdk_files or {}

    user_data = load_template(user_data_template).format(
        agent_py=indent_yaml(agent_py, 6),
        agent_verify_py=indent_yaml(agent_verify_py, 6),
        agent_ratls_py=indent_yaml(agent_ratls_py, 6),
        control_plane_init_py=indent_yaml(control_plane_files.get("init", ""), 6),
        control_plane_server_py=indent_yaml(control_plane_files.get("server", ""), 6),
        control_plane_config_py=indent_yaml(control_plane_files.get("config", ""), 6),
        control_plane_allowlist_py=indent_yaml(control_plane_files.get("allowlist", ""), 6),
        control_plane_ledger_py=indent_yaml(control_plane_files.get("ledger", ""), 6),
        control_plane_policy_py=indent_yaml(control_plane_files.get("policy", ""), 6),
        control_plane_registry_py=indent_yaml(control_plane_files.get("registry", ""), 6),
        control_plane_ratls_py=indent_yaml(control_plane_files.get("ratls", ""), 6),
        control_plane_admin_html=indent_yaml(control_plane_files.get("admin_html", ""), 6),
        sdk_init_py=indent_yaml(sdk_files.get("init", ""), 6),
        sdk_connect_py=indent_yaml(sdk_files.get("connect", ""), 6),
        sdk_exceptions_py=indent_yaml(sdk_files.get("exceptions", ""), 6),
        sdk_github_py=indent_yaml(sdk_files.get("github", ""), 6),
        sdk_ratls_py=indent_yaml(sdk_files.get("ratls", ""), 6),
        sdk_verify_py=indent_yaml(sdk_files.get("verify", ""), 6),
        agent_env=indent_yaml(agent_env, 6),
        nginx_conf=indent_yaml(nginx_conf, 6),
        agent_service=indent_yaml(agent_service, 6),
        vm_image_id=vm_image_id,
    )

    user_data_path = os.path.join(workdir, "user-data")
    meta_data_path = os.path.join(workdir, "meta-data")
    network_config_path = os.path.join(workdir, "network-config")

    with open(user_data_path, 'w') as f:
        f.write(user_data)
    with open(meta_data_path, 'w') as f:
        f.write("instance-id: ee-agent\nlocal-hostname: ee-agent\n")
    with open(network_config_path, 'w') as f:
        f.write(network_config)

    cidata_iso = os.path.join(workdir, "cidata.iso")
    subprocess.run([
        'genisoimage', '-output', cidata_iso,
        '-volid', 'cidata', '-joliet', '-rock',
        user_data_path, meta_data_path, network_config_path
    ], check=True, capture_output=True)

    for f in os.listdir(workdir):
        filepath = os.path.join(workdir, f)
        if f.endswith('.qcow2'):
            os.chmod(filepath, 0o666)
        else:
            os.chmod(filepath, 0o644)

    return agent_image, cidata_iso, workdir


def create_agent_vm(
    name: str = "ee-attestor",
    port: int = 8000,
    host_port: int | None = None,
    public_port: int = 443,
    admin_port: int = 8080,
    proxy_port: int = 9090,
    vm_image_tag: str = "",
    vm_image_sha256: str = "",
    base_image: str | None = None,
    public_ip: str | None = None,
    control_plane_enabled: bool = False,
) -> dict:
    """Create an agent VM with no workload compose."""
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

    repo_root = Path(__file__).resolve().parent.parent
    agent_py = (repo_root / "agent" / "agent.py").read_text(encoding="utf-8")
    agent_verify_py = (repo_root / "agent" / "verify.py").read_text(encoding="utf-8")
    agent_ratls_py = (repo_root / "agent" / "ratls.py").read_text(encoding="utf-8")
    control_plane_root = repo_root / "control_plane"
    control_plane_files = {
        "init": "",
        "server": (control_plane_root / "server.py").read_text(encoding="utf-8"),
        "config": (control_plane_root / "config.py").read_text(encoding="utf-8"),
        "allowlist": (control_plane_root / "allowlist.py").read_text(encoding="utf-8"),
        "ledger": (control_plane_root / "ledger.py").read_text(encoding="utf-8"),
        "policy": (control_plane_root / "policy.py").read_text(encoding="utf-8"),
        "registry": (control_plane_root / "registry.py").read_text(encoding="utf-8"),
        "ratls": (control_plane_root / "ratls.py").read_text(encoding="utf-8"),
        "admin_html": (control_plane_root / "static" / "admin.html").read_text(encoding="utf-8"),
    }
    sdk_root = repo_root / "sdk" / "easyenclave"
    sdk_files = {
        "init": (sdk_root / "__init__.py").read_text(encoding="utf-8"),
        "connect": (sdk_root / "connect.py").read_text(encoding="utf-8"),
        "exceptions": (sdk_root / "exceptions.py").read_text(encoding="utf-8"),
        "github": (sdk_root / "github.py").read_text(encoding="utf-8"),
        "ratls": (sdk_root / "ratls.py").read_text(encoding="utf-8"),
        "verify": (sdk_root / "verify.py").read_text(encoding="utf-8"),
    }
    base_env = {
        "EE_MAIN_BIND": "0.0.0.0",
        "EE_MAIN_PORT": str(port),
        "EE_ADMIN_BIND": "127.0.0.1",
        "EE_ADMIN_PORT": str(admin_port),
        "EE_PROXY_BIND": "127.0.0.1",
        "EE_PROXY_PORT": str(proxy_port),
        "EE_CONTROL_PLANE_ENABLED": "true" if control_plane_enabled else "false",
        "SEAL_VM": "true",
    }
    extra_env = {
        key: value
        for key, value in os.environ.items()
        if key.startswith("EE_") or key.startswith("CLOUDFLARE_")
    }
    for key in list(base_env.keys()):
        extra_env.pop(key, None)
    agent_env = build_agent_env(base_env, extra_env)
    admin_tls_port = 9443
    nginx_conf = load_template("nginx.conf").format(
        main_port=port,
        admin_port=admin_port,
        admin_tls_port=admin_tls_port,
        admin_cert_path="/etc/nginx/ssl/admin.crt",
        admin_key_path="/etc/nginx/ssl/admin.key",
    )
    log("Creating agent image...")
    agent_image, cidata_iso, workdir = create_agent_image(
        base_image,
        agent_py,
        agent_verify_py,
        agent_ratls_py,
        vm_image_tag,
        vm_image_sha256,
        agent_port=port,
        agent_env=agent_env,
        nginx_conf=nginx_conf,
        control_plane_files=control_plane_files,
        sdk_files=sdk_files,
    )

    log("Starting agent VM...")
    ip = start_td_vm(agent_image, cidata_iso, name)
    log(f"Agent VM IP: {ip}")

    log("Waiting for agent to be ready...")
    try:
        wait_for_ready(ip, port=port, timeout=600)
    except TimeoutError as exc:
        log(str(exc))
        log_serial_tail(name)
        raise

    log("Setting up port forwarding...")
    host_port = setup_port_forward(ip, public_port, host_port or public_port, public_ip)

    return {
        "name": name,
        "ip": ip,
        "port": port,
        "public_port": public_port,
        "host_port": host_port,
        "workdir": workdir,
        "image": agent_image,
    }


def create_minimal_cidata(
    workdir: str,
    hostname: str = "ee-agent",
    agent_env: str | None = None,
) -> str:
    """Create a minimal cloud-init ISO for networking/metadata."""
    os.makedirs(workdir, exist_ok=True)
    os.chmod(workdir, 0o755)
    user_data_path = os.path.join(workdir, "user-data")
    meta_data_path = os.path.join(workdir, "meta-data")
    network_config_path = os.path.join(workdir, "network-config")

    user_data = "#cloud-config\n"
    if agent_env:
        env_body = agent_env.rstrip("\n")
        user_data += (
            "write_files:\n"
            "  - path: /etc/easy-enclave/agent.env\n"
            "    permissions: '0640'\n"
            "    content: |\n"
            f"{indent_yaml(env_body, 6)}\n"
            "runcmd:\n"
            "  - systemctl restart ee-agent || true\n"
            "  - systemctl restart nginx || true\n"
        )
    with open(user_data_path, "w") as f:
        f.write(user_data)
    with open(meta_data_path, "w") as f:
        f.write(f"instance-id: {hostname}\nlocal-hostname: {hostname}\n")
    with open(network_config_path, "w") as f:
        f.write(load_template("network-config.yml"))

    for path in (user_data_path, meta_data_path, network_config_path):
        os.chmod(path, 0o644)

    cidata_iso = os.path.join(workdir, "cidata.iso")
    subprocess.run([
        "genisoimage", "-output", cidata_iso,
        "-volid", "cidata", "-joliet", "-rock",
        user_data_path, meta_data_path, network_config_path
    ], check=True, capture_output=True)
    os.chmod(cidata_iso, 0o644)
    return cidata_iso


def start_agent_vm_from_image(
    image_path: str,
    name: str = "ee-attestor",
    port: int = 8000,
    host_port: int | None = None,
    public_port: int = 443,
    admin_port: int = 8080,
    proxy_port: int = 9090,
    public_ip: str | None = None,
    control_plane_enabled: bool = False,
) -> dict:
    """Start an agent VM from a pre-baked image."""
    log("Checking requirements...")
    check_requirements()

    workdir = tempfile.mkdtemp(prefix="ee-agent-boot-")
    base_env = {
        "EE_MAIN_BIND": "0.0.0.0",
        "EE_MAIN_PORT": str(port),
        "EE_ADMIN_BIND": "127.0.0.1",
        "EE_ADMIN_PORT": str(admin_port),
        "EE_PROXY_BIND": "127.0.0.1",
        "EE_PROXY_PORT": str(proxy_port),
        "EE_CONTROL_PLANE_ENABLED": "true" if control_plane_enabled else "false",
        "SEAL_VM": "true",
    }
    extra_env = {
        key: value
        for key, value in os.environ.items()
        if key.startswith("EE_") or key.startswith("CLOUDFLARE_")
    }
    for key in list(base_env.keys()):
        extra_env.pop(key, None)
    agent_env = build_agent_env(base_env, extra_env)
    cidata_iso = create_minimal_cidata(workdir, hostname=name, agent_env=agent_env)

    # Create a VM-specific overlay so multiple VMs are not competing for write
    # access to the same pristine image.
    os.makedirs("/var/lib/easy-enclave", exist_ok=True)
    vm_image = f"/var/lib/easy-enclave/{name}.qcow2"
    try:
        os.remove(vm_image)
    except FileNotFoundError:
        pass
    subprocess.run(
        [
            "qemu-img",
            "create",
            "-f",
            "qcow2",
            "-F",
            "qcow2",
            "-b",
            image_path,
            vm_image,
        ],
        check=True,
        capture_output=True,
    )
    os.chmod(vm_image, 0o666)

    log(f"Starting agent VM from image: {vm_image} (base: {image_path})")
    ip = start_td_vm(vm_image, cidata_iso, name)
    log(f"Agent VM IP: {ip}")

    log("Waiting for agent to be ready...")
    if port != 8000:
        log("Warning: agent images are often built for port 8000; ensure the image matches --port")
    try:
        wait_for_ready(ip, port=port, timeout=600)
    except TimeoutError as exc:
        log(str(exc))
        log_serial_tail(name)
        raise

    log("Setting up port forwarding...")
    host_port = setup_port_forward(ip, public_port, host_port or public_port, public_ip)

    return {
        "name": name,
        "ip": ip,
        "port": port,
        "public_port": public_port,
        "host_port": host_port,
        "workdir": workdir,
        "image": vm_image,
    }


def wait_for_vm_shutdown(name: str, timeout: int = 1200) -> None:
    """Wait for a VM to reach the shut off state."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = subprocess.run(['sudo', 'virsh', 'domstate', name], capture_output=True, text=True)
        state = result.stdout.strip().lower()
        if "shut off" in state or "shutoff" in state:
            return
        time.sleep(5)
    raise TimeoutError(f"Timed out waiting for {name} to shut down")


def log_serial_tail(name: str, lines: int = 200) -> None:
    path = Path(f"/var/log/libvirt/qemu/{name}-serial.log")
    if not path.exists():
        log(f"No serial log found at {path}")
        return
    try:
        content = path.read_text(errors="replace").splitlines()
    except Exception as exc:
        log(f"Failed to read serial log: {exc}")
        return
    tail_lines = content[-lines:] if lines > 0 else content
    if not tail_lines:
        log("Serial log is empty")
        return
    log("=== Serial log tail ===")
    for line in tail_lines:
        log(line)


def cleanup_vm_definition(name: str) -> None:
    """Remove VM definition without deleting disk."""
    subprocess.run(['sudo', 'virsh', 'destroy', name], capture_output=True)
    subprocess.run(['sudo', 'virsh', 'undefine', name, '--nvram'], capture_output=True)


def build_pristine_agent_image(
    name: str = "ee-agent-bake",
    vm_image_tag: str = "",
    vm_image_sha256: str = "",
    tdx_repo_dir: str | None = None,
    tdx_repo_ref: str = "main",
    tdx_guest_version: str = DEFAULT_TDX_GUEST_VERSION,
    agent_port: int = 8000,
    output_path: str | None = None,
    timeout: int = 3600,
) -> dict:
    """Build a pristine agent image by baking cloud-init into a base TD image."""
    log("Checking requirements...")
    check_requirements()

    log("Building TD base image via canonical/tdx...")
    base_image = find_or_download_td_image(
        prefer_version=tdx_guest_version,
        build_from_tdx_repo=True,
        tdx_repo_dir=tdx_repo_dir,
        tdx_repo_ref=tdx_repo_ref,
    )
    log(f"Using base image: {base_image}")
    if not vm_image_sha256:
        vm_image_sha256 = sha256_file(base_image)
        log(f"Computed base image sha256: {vm_image_sha256}")

    repo_root = Path(__file__).resolve().parent.parent
    agent_py = (repo_root / "agent" / "agent.py").read_text(encoding="utf-8")
    agent_verify_py = (repo_root / "agent" / "verify.py").read_text(encoding="utf-8")
    agent_ratls_py = (repo_root / "agent" / "ratls.py").read_text(encoding="utf-8")
    control_plane_root = repo_root / "control_plane"
    control_plane_files = {
        "init": "",
        "server": (control_plane_root / "server.py").read_text(encoding="utf-8"),
        "config": (control_plane_root / "config.py").read_text(encoding="utf-8"),
        "allowlist": (control_plane_root / "allowlist.py").read_text(encoding="utf-8"),
        "ledger": (control_plane_root / "ledger.py").read_text(encoding="utf-8"),
        "policy": (control_plane_root / "policy.py").read_text(encoding="utf-8"),
        "registry": (control_plane_root / "registry.py").read_text(encoding="utf-8"),
        "ratls": (control_plane_root / "ratls.py").read_text(encoding="utf-8"),
        "admin_html": (control_plane_root / "static" / "admin.html").read_text(encoding="utf-8"),
    }
    sdk_root = repo_root / "sdk" / "easyenclave"
    sdk_files = {
        "init": (sdk_root / "__init__.py").read_text(encoding="utf-8"),
        "connect": (sdk_root / "connect.py").read_text(encoding="utf-8"),
        "exceptions": (sdk_root / "exceptions.py").read_text(encoding="utf-8"),
        "github": (sdk_root / "github.py").read_text(encoding="utf-8"),
        "ratls": (sdk_root / "ratls.py").read_text(encoding="utf-8"),
        "verify": (sdk_root / "verify.py").read_text(encoding="utf-8"),
    }
    base_env = {
        "EE_MAIN_BIND": "0.0.0.0",
        "EE_MAIN_PORT": str(agent_port),
        "EE_ADMIN_BIND": "127.0.0.1",
        "EE_ADMIN_PORT": "8080",
        "EE_PROXY_BIND": "127.0.0.1",
        "EE_PROXY_PORT": "9090",
        "EE_CONTROL_PLANE_ENABLED": "false",
        "SEAL_VM": "true",
    }
    agent_env = build_agent_env(base_env)
    admin_tls_port = 9443
    nginx_conf = load_template("nginx.conf").format(
        main_port=agent_port,
        admin_port=8080,
        admin_tls_port=admin_tls_port,
        admin_cert_path="/etc/nginx/ssl/admin.crt",
        admin_key_path="/etc/nginx/ssl/admin.key",
    )
    log("Creating agent bake image...")
    agent_image, cidata_iso, workdir = create_agent_image(
        base_image,
        agent_py,
        agent_verify_py,
        agent_ratls_py,
        vm_image_tag,
        vm_image_sha256,
        agent_port=agent_port,
        agent_env=agent_env,
        nginx_conf=nginx_conf,
        control_plane_files=control_plane_files,
        sdk_files=sdk_files,
        user_data_template="agent-bake-user-data.yml",
    )

    log("Starting bake VM...")
    ip = start_td_vm(agent_image, cidata_iso, name)
    log(f"Bake VM IP: {ip}")

    log("Waiting for bake VM to shut down...")
    try:
        wait_for_vm_shutdown(name, timeout=timeout)
    except TimeoutError as exc:
        log(str(exc))
        log_serial_tail(name)
        cleanup_vm_definition(name)
        raise
    log_serial_tail(name)
    cleanup_vm_definition(name)

    os.makedirs(IMAGE_DIR, exist_ok=True)
    if output_path:
        dest_path = output_path
    else:
        tag = vm_image_tag or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        dest_path = os.path.join(IMAGE_DIR, f"agent-pristine-{tag}.qcow2")

    log(f"Exporting pristine image to {dest_path}...")
    subprocess.run(['qemu-img', 'convert', '-O', 'qcow2', agent_image, dest_path], check=True)
    os.chmod(dest_path, 0o666)

    return {
        "name": name,
        "base_image": base_image,
        "agent_image": dest_path,
        "workdir": workdir,
    }

def indent_yaml(content: str, spaces: int) -> str:
    """Indent YAML content."""
    indent = ' ' * spaces
    return '\n'.join(indent + line for line in content.split('\n'))


def start_td_vm(
    workload_image: str,
    cidata_iso: str,
    name: str = "ee-workload",
    memory_mb: int = 4096,
    vcpus: int = 2,
) -> str:
    """
    Start a TD VM using Canonical's approach.

    Returns the VM's IP address.
    """
    # Check for tdvirsh (Canonical tool)
    tdvirsh = os.path.expanduser("~/tdx/guest-tools/run_td.sh")
    if not os.path.exists(tdvirsh):
        tdvirsh = "/opt/tdx/guest-tools/run_td.sh"

    # First, try using libvirt directly with TDX support
    vm_xml = generate_tdx_domain_xml(name, workload_image, cidata_iso, memory_mb, vcpus)

    fd, xml_path = tempfile.mkstemp(prefix=f"{name}-", suffix=".xml")
    with os.fdopen(fd, 'w') as f:
        f.write(vm_xml)

    # Clean up existing VM thoroughly
    log(f"Cleaning up existing VM {name}...")
    subprocess.run(['sudo', 'virsh', 'destroy', name], capture_output=True)
    subprocess.run(['sudo', 'virsh', 'undefine', name, '--nvram'], capture_output=True)

    # Wait a moment for cleanup
    time.sleep(1)

    # Verify cleanup
    check = subprocess.run(['sudo', 'virsh', 'domstate', name], capture_output=True, text=True)
    if check.returncode == 0:
        log(f"Warning: VM {name} still exists, forcing undefine...")
        subprocess.run(['sudo', 'virsh', 'undefine', name, '--nvram', '--remove-all-storage'], capture_output=True)
        time.sleep(1)

    # Define and start
    result = subprocess.run(['sudo', 'virsh', 'define', xml_path], capture_output=True, text=True)
    if result.returncode != 0:
        log(f"virsh define failed: {result.stderr}")
        raise RuntimeError(f"Failed to define VM: {result.stderr}")

    result = subprocess.run(['sudo', 'virsh', 'start', name], capture_output=True, text=True)
    if result.returncode != 0:
        log(f"virsh start failed: {result.stderr}")
        raise RuntimeError(f"Failed to start VM: {result.stderr}")

    log(f"VM {name} started successfully")

    # Give VM a moment to boot
    time.sleep(10)

    # Check VM state
    result = subprocess.run(['sudo', 'virsh', 'domstate', name], capture_output=True, text=True)
    log(f"VM state: {result.stdout.strip()}")

    # Dump actual XML to see what libvirt created
    result = subprocess.run(['sudo', 'virsh', 'dumpxml', name], capture_output=True, text=True)
    log("=== Actual VM XML (interface section) ===")
    for line in result.stdout.split('\n'):
        if 'interface' in line.lower() or 'source network' in line.lower() or 'model type' in line.lower() or 'mac address' in line.lower():
            log(line)

    # Check network bridge
    result = subprocess.run(['ip', 'link', 'show', 'virbr0'], capture_output=True, text=True)
    log(f"virbr0 status: {result.stdout.strip() if result.returncode == 0 else 'not found'}")

    # Check DHCP leases
    result = subprocess.run(['sudo', 'virsh', 'net-dhcp-leases', 'default'], capture_output=True, text=True)
    log(f"DHCP leases:\n{result.stdout}")

    # Check ARP table for any new entries (use ip neigh instead of arp)
    result = subprocess.run(['ip', 'neigh'], capture_output=True, text=True)
    log(f"ARP/Neighbor table:\n{result.stdout}")

    # Try to get console log to see boot status
    log("=== Checking VM console/serial log ===")
    try:
        # Check qemu log if available
        result = subprocess.run(['sudo', 'cat', f'/var/log/libvirt/qemu/{name}.log'],
                               capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            lines = result.stdout.strip().split('\n')
            log("Last 10 lines of QEMU log:")
            for line in lines[-10:]:
                log(f"  {line}")
    except Exception as e:
        log(f"Could not read QEMU log: {e}")

    # Wait for IP
    ip = wait_for_vm_ip(name)
    return ip


def generate_tdx_domain_xml(
    name: str,
    disk_path: str,
    cidata_iso: str,
    memory_mb: int,
    vcpus: int,
) -> str:
    """Generate libvirt XML for TDX VM based on Canonical's template."""
    ovmf_paths = [
        "/usr/share/ovmf/OVMF.tdx.fd",
        "/usr/share/qemu/OVMF.fd",
        "/usr/share/ovmf/OVMF.fd",
        "/usr/share/OVMF/OVMF_CODE_4M.fd",
    ]
    ovmf = next((p for p in ovmf_paths if os.path.exists(p)), ovmf_paths[0])
    log(f"Using OVMF firmware: {ovmf}")

    return load_template("domain.xml").format(
        name=name,
        memory_mb=memory_mb,
        vcpus=vcpus,
        ovmf=ovmf,
        disk_path=disk_path,
        cidata_iso=cidata_iso,
    )


def get_vm_mac(name: str) -> str:
    """Get the MAC address of a VM's network interface."""
    result = subprocess.run(
        ['sudo', 'virsh', 'domiflist', name],
        capture_output=True, text=True
    )
    for line in result.stdout.split('\n'):
        # Look for lines with MAC addresses (format: 52:54:00:xx:xx:xx)
        parts = line.split()
        for part in parts:
            if ':' in part and len(part) == 17 and part.count(':') == 5:
                return part.lower()
    return ""


def wait_for_vm_ip(name: str, timeout: int = 300) -> str:
    """Wait for VM to get an IP address."""
    start = time.time()
    last_print = 0

    # Get the VM's MAC address first
    vm_mac = get_vm_mac(name)
    if vm_mac:
        log(f"VM MAC address: {vm_mac}")
    else:
        log("Warning: Could not get VM MAC address")

    while time.time() - start < timeout:
        elapsed = int(time.time() - start)
        if elapsed - last_print >= 30:
            last_print = elapsed
            log(f"Waiting for VM IP... ({elapsed}s elapsed)")
            # Show DHCP leases periodically
            result = subprocess.run(['sudo', 'virsh', 'net-dhcp-leases', 'default'],
                                   capture_output=True, text=True)
            if result.stdout.strip():
                lease_lines = [line for line in result.stdout.split('\n') if '192.168.' in line]
                if lease_lines:
                    log(f"  DHCP leases: {len(lease_lines)} found")
                    for lease_line in lease_lines[:3]:
                        log(f"    {lease_line.strip()}")

        # Try virsh domifaddr with agent
        try:
            result = subprocess.run(
                ['sudo', 'virsh', 'domifaddr', name, '--source', 'agent'],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.split('\n'):
                if 'ipv4' in line.lower():
                    parts = line.split()
                    for part in parts:
                        if '/' in part and '.' in part:
                            return part.split('/')[0]
        except Exception:
            pass

        # Try virsh domifaddr without agent
        try:
            result = subprocess.run(
                ['sudo', 'virsh', 'domifaddr', name],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.split('\n'):
                parts = line.split()
                for part in parts:
                    if '/' in part and '.' in part and part.startswith('192.'):
                        return part.split('/')[0]
        except Exception:
            pass

        # Try virsh net-dhcp-leases - match by MAC address ONLY to avoid stale hostname matches
        try:
            result = subprocess.run(
                ['sudo', 'virsh', 'net-dhcp-leases', 'default'],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.split('\n'):
                line_lower = line.lower()
                # Match by MAC address ONLY - hostname can be stale from previous VMs
                if vm_mac and vm_mac in line_lower:
                    parts = line.split()
                    for part in parts:
                        if '/' in part and '.' in part and part.startswith('192.'):
                            ip = part.split('/')[0]
                            log(f"Found IP {ip} for VM {name} (MAC: {vm_mac})")
                            return ip
        except Exception:
            pass

        time.sleep(10)

    raise TimeoutError(f"VM {name} did not get IP within {timeout}s")


def get_public_ip() -> str:
    """Get the host's public IP address."""
    # Try multiple methods
    methods = [
        # Check for public IP on interfaces
        lambda: subprocess.run(
            ['hostname', '-I'],
            capture_output=True, text=True, timeout=5
        ).stdout.split()[0] if not subprocess.run(
            ['hostname', '-I'], capture_output=True, text=True, timeout=5
        ).stdout.split()[0].startswith(('192.168.', '10.', '172.')) else None,
        # Use external service
        lambda: urllib.request.urlopen('https://ifconfig.me', timeout=5).read().decode().strip(),
        lambda: urllib.request.urlopen('https://api.ipify.org', timeout=5).read().decode().strip(),
    ]

    for method in methods:
        try:
            ip = method()
            if ip and not ip.startswith(('192.168.', '10.', '172.16.', '172.17.', '172.18.')):
                return ip
        except Exception:
            continue

    # Fallback: get first non-private IP from hostname -I
    try:
        result = subprocess.run(['hostname', '-I'], capture_output=True, text=True, timeout=5)
        for ip in result.stdout.split():
            if not ip.startswith(('192.168.', '10.', '172.', '127.')):
                return ip
    except Exception:
        pass

    raise RuntimeError("Could not determine public IP address")


def setup_port_forward(
    vm_ip: str,
    vm_port: int,
    host_port: int = None,
    public_ip: str | None = None,
) -> int:
    """
    Set up iptables port forwarding from host to VM.

    Args:
        vm_ip: VM's private IP address
        vm_port: Port on the VM to forward to
        host_port: Port on the host (defaults to vm_port)
        public_ip: Optional public IP to bind DNAT rules to

    Returns:
        The host port that was configured
    """
    host_port = host_port or vm_port
    public_ip = public_ip or None

    def remove_nat_rules(chain: str) -> None:
        result = subprocess.run(
            ['sudo', 'iptables', '-t', 'nat', '-L', chain, '--line-numbers'],
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
            if f"dpt:{host_port}" not in line or "DNAT" not in line:
                continue
            if public_ip and public_ip not in line:
                continue
            rule_numbers.append(int(parts[0]))
        for number in reversed(rule_numbers):
            subprocess.run(
                ['sudo', 'iptables', '-t', 'nat', '-D', chain, str(number)],
                capture_output=True,
            )

    def remove_forward_rules() -> None:
        for chain in ("FORWARD", "LIBVIRT_FWI"):
            result = subprocess.run(
                ['sudo', 'iptables', '-L', chain, '--line-numbers'],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                continue
            rule_numbers = []
            for line in result.stdout.splitlines():
                parts = line.split()
                if not parts or not parts[0].isdigit():
                    continue
                if f"dpt:{vm_port}" in line and vm_ip in line and "ACCEPT" in line:
                    rule_numbers.append(int(parts[0]))
            for number in reversed(rule_numbers):
                subprocess.run(
                    ['sudo', 'iptables', '-D', chain, str(number)],
                    capture_output=True,
                )

    def add_nat_rule(chain: str, destination: str | None) -> None:
        cmd = ['sudo', 'iptables', '-t', 'nat', '-A', chain, '-p', 'tcp']
        if destination:
            cmd.extend(['-d', destination])
        cmd.extend([
            '--dport', str(host_port),
            '-j', 'DNAT', '--to-destination', f'{vm_ip}:{vm_port}',
        ])
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to add {chain} rule: {result.stderr}")

    # Remove any existing rules for this port first
    remove_nat_rules('PREROUTING')
    remove_nat_rules('OUTPUT')
    remove_forward_rules()

    # Add PREROUTING rule for incoming traffic (insert at top to avoid stale rules)
    result = subprocess.run(
        ['sudo', 'iptables', '-t', 'nat', '-I', 'PREROUTING', '1', '-p', 'tcp']
        + (['-d', public_ip] if public_ip else [])
        + ['--dport', str(host_port), '-j', 'DNAT', '--to-destination', f'{vm_ip}:{vm_port}'],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to insert PREROUTING rule: {result.stderr}")

    # Add OUTPUT rule so local traffic can reach the VM (used by SSH attestation)
    output_destination = public_ip if public_ip else '127.0.0.1'
    try:
        add_nat_rule('OUTPUT', output_destination)
    except RuntimeError as exc:
        log(f"Warning: Failed to add OUTPUT rule: {exc}")

    # Allow inbound traffic to virbr0 before libvirt's default reject.
    result = subprocess.run([
        'sudo', 'iptables', '-I', 'LIBVIRT_FWI', '1',
        '-p', 'tcp', '-d', vm_ip, '--dport', str(vm_port),
        '-j', 'ACCEPT'
    ], capture_output=True, text=True)
    if result.returncode != 0:
        log(f"Warning: Failed to add LIBVIRT_FWI rule: {result.stderr}")

    destination_desc = public_ip if public_ip else '*'
    log(f"Port forwarding configured: {destination_desc}:{host_port} -> {vm_ip}:{vm_port}")
    return host_port


def wait_for_ready(ip: str, port: int = 8080, timeout: int = 300) -> None:
    """Wait for workload to be ready by checking port."""
    import socket
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
    """
    Create a TD VM with the given workload.

    Returns dict with IP and quote. Raises on failure.
    """
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


def destroy_td_vm(name: str = "ee-workload") -> None:
    """Destroy a TD VM."""
    subprocess.run(['sudo', 'virsh', 'destroy', name], capture_output=True)
    subprocess.run(['sudo', 'virsh', 'undefine', name], capture_output=True)


def cleanup_td_vms(prefixes: Sequence[str] | None = None) -> None:
    """Destroy any TD VMs whose names match the provided prefixes."""
    if prefixes is None:
        prefixes = ("ee-deploy-", "ee-workload", "ee-")
    elif isinstance(prefixes, str):
        prefixes = (prefixes,)
    else:
        prefixes = tuple(prefixes)
    result = subprocess.run(
        ['sudo', 'virsh', 'list', '--all', '--name'],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log(f"Warning: failed to list VMs: {result.stderr.strip()}")
        return
    for name in [line.strip() for line in result.stdout.splitlines() if line.strip()]:
        if not name.startswith(prefixes):
            continue
        log(f"Cleaning up existing VM {name}...")
        subprocess.run(['sudo', 'virsh', 'destroy', name], capture_output=True)
        subprocess.run(['sudo', 'virsh', 'undefine', name, '--nvram', '--remove-all-storage'], capture_output=True)


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
    except Exception as e:
        log(f"Warning: release cleanup failed: {e}")


def create_release(
    quote: str,
    endpoint: str,
    repo: str = None,
    token: str = None,
    seal_vm: bool = False,
) -> str:
    """Create a GitHub release with attestation data."""
    repo = repo or os.environ.get('GITHUB_REPOSITORY')
    token = token or os.environ.get('GITHUB_TOKEN')

    if not repo or not token:
        raise ValueError("GITHUB_REPOSITORY and GITHUB_TOKEN must be set")

    cleanup_deploy_releases(repo, token)

    now = datetime.now(timezone.utc)
    timestamp = now.strftime('%Y%m%d-%H%M%S')
    tag = f"deploy-{timestamp}"

    attestation = {
        "version": "1.0",
        "quote": quote,
        "endpoint": endpoint,
        "timestamp": now.isoformat().replace('+00:00', 'Z'),
        "repo": repo,
        "sealed": seal_vm,
    }

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


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Create TD VM with workload')
    parser.add_argument('docker_compose', nargs='?', help='Path to docker-compose.yml')
    parser.add_argument('--name', default='ee-workload', help='VM name (default: ee-workload)')
    parser.add_argument('--port', type=int, default=None, help='HTTP port for workload or agent')
    parser.add_argument('--host-port', type=int, default=None, help='Host port to forward to agent (default: same as --port)')
    parser.add_argument('--public-port', type=int, default=None, help='VM port to expose publicly (default: 443 for agent)')
    parser.add_argument('--admin-port', type=int, default=8080, help='Agent admin HTTP port inside the VM')
    parser.add_argument('--proxy-port', type=int, default=9090, help='Control-plane proxy port inside the VM')
    parser.add_argument('--public-ip', default='', help='Public IP to bind DNAT rules to (optional)')
    parser.add_argument('--enable-ssh', action='store_true', help='Enable SSH access (default: off)')
    parser.add_argument('--create-release', action='store_true', help='Create GitHub release with attestation')
    parser.add_argument('--endpoint', help='Endpoint URL for release (default: http://{vm_ip}:{port})')
    parser.add_argument('--agent', action='store_true', help='Create agent VM (no workload)')
    parser.add_argument('--agent-image', default='', help='Start agent VM from a pre-baked image')
    parser.add_argument('--control-plane', action='store_true', help='Enable control-plane endpoints in agent VM')
    parser.add_argument('--vm-image-tag', default='', help='Agent VM image tag')
    parser.add_argument('--vm-image-sha256', default='', help='Agent VM image sha256')
    parser.add_argument('--base-image', default='', help='Base TD image path override')
    parser.add_argument('--build-pristine-agent-image', action='store_true', help='Bake a pristine agent image')
    parser.add_argument('--tdx-repo-dir', default='', help='canonical/tdx repo dir for image build')
    parser.add_argument('--tdx-repo-ref', default='main', help='canonical/tdx repo ref (default: main)')
    parser.add_argument('--tdx-guest-version', default=DEFAULT_TDX_GUEST_VERSION, help='TD guest Ubuntu version')
    parser.add_argument('--output-image', default='', help='Output path for pristine agent image')
    parser.add_argument('--bake-timeout', type=int, default=3600, help='Bake timeout seconds (default: 3600)')
    args = parser.parse_args()

    is_agent_mode = args.agent or args.build_pristine_agent_image
    port = args.port
    if port is None:
        port = 8000 if is_agent_mode else 8080
    public_port = args.public_port
    if public_port is None:
        public_port = 443 if is_agent_mode else port

    if args.build_pristine_agent_image:
        result = build_pristine_agent_image(
            name=args.name if args.name != 'ee-workload' else 'ee-agent-bake',
            vm_image_tag=args.vm_image_tag,
            vm_image_sha256=args.vm_image_sha256,
            tdx_repo_dir=args.tdx_repo_dir or None,
            tdx_repo_ref=args.tdx_repo_ref,
            tdx_guest_version=args.tdx_guest_version,
            agent_port=port,
            output_path=args.output_image or None,
            timeout=args.bake_timeout,
        )
        print(json.dumps(result, indent=2))
        sys.exit(0)

    if args.agent:
        if args.name == 'ee-workload':
            args.name = 'ee-attestor'
        if args.agent_image:
            result = start_agent_vm_from_image(
                image_path=args.agent_image,
                name=args.name,
                port=port,
                host_port=args.host_port,
                public_port=public_port,
                admin_port=args.admin_port,
                proxy_port=args.proxy_port,
                public_ip=args.public_ip or None,
                control_plane_enabled=args.control_plane,
            )
        else:
            result = create_agent_vm(
                name=args.name,
                port=port,
                host_port=args.host_port,
                public_port=public_port,
                admin_port=args.admin_port,
                proxy_port=args.proxy_port,
                vm_image_tag=args.vm_image_tag,
                vm_image_sha256=args.vm_image_sha256,
                base_image=args.base_image or None,
                public_ip=args.public_ip or None,
                control_plane_enabled=args.control_plane,
            )
        print(json.dumps(result, indent=2))
        sys.exit(0)

    if not args.docker_compose:
        parser.error('docker_compose is required unless --agent is used')
    docker_compose = args.docker_compose

    result = create_td_vm(
        docker_compose,
        name=args.name,
        port=port,
        enable_ssh=args.enable_ssh,
        base_image=args.base_image or None,
    )

    if args.create_release:
        endpoint = args.endpoint or f"http://{result['ip']}:{result['port']}"
        release_url = create_release(result['quote'], endpoint)
        result['release_url'] = release_url

    # Only JSON goes to stdout, logs went to stderr
    print(json.dumps(result, indent=2))
