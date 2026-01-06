#!/usr/bin/env python3
"""
TD VM management using Canonical TDX tooling.

Uses tdvirsh for VM management and trustauthority-cli for quote generation.
See: https://github.com/canonical/tdx
"""

import hashlib
import json
import os
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
    _script_dir.parent / "templates",      # action/src/vm.py -> action/templates
    _script_dir / "templates",             # /opt/easy-enclave/vm.py -> /opt/easy-enclave/templates
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
    cmd = ["./create-td-image.sh", "-v", version]
    if os.geteuid() != 0:
        cmd = ["sudo"] + cmd
    log(f"Building TD image via {image_dir} ({version})...")
    subprocess.run(cmd, check=True, cwd=image_dir, capture_output=True)
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


def create_agent_image(
    base_image: str,
    agent_py: str,
    vm_py: str,
    vm_image_tag: str,
    vm_image_sha256: str,
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

    agent_service = load_template("agent-service.service")
    network_config = load_template("network-config.yml")
    vm_image_id = build_vm_image_id_yaml(vm_image_tag, vm_image_sha256)

    user_data = load_template(user_data_template).format(
        agent_py=indent_yaml(agent_py, 6),
        vm_py=indent_yaml(vm_py, 6),
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
    vm_image_tag: str = "",
    vm_image_sha256: str = "",
    base_image: str | None = None,
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

    agent_py = (Path(__file__).parent / "agent.py").read_text()
    vm_py = Path(__file__).read_text()

    log("Creating agent image...")
    agent_image, cidata_iso, workdir = create_agent_image(
        base_image,
        agent_py,
        vm_py,
        vm_image_tag,
        vm_image_sha256,
    )

    log("Starting agent VM...")
    ip = start_td_vm(agent_image, cidata_iso, name)
    log(f"Agent VM IP: {ip}")

    log("Waiting for agent to be ready...")
    wait_for_ready(ip, port=port, timeout=300)

    log("Setting up port forwarding...")
    host_port = setup_port_forward(ip, port)

    return {
        "name": name,
        "ip": ip,
        "port": port,
        "host_port": host_port,
        "workdir": workdir,
    }


def create_minimal_cidata(workdir: str, hostname: str = "ee-agent") -> str:
    """Create a minimal cloud-init ISO for networking/metadata."""
    os.makedirs(workdir, exist_ok=True)
    os.chmod(workdir, 0o755)
    user_data_path = os.path.join(workdir, "user-data")
    meta_data_path = os.path.join(workdir, "meta-data")
    network_config_path = os.path.join(workdir, "network-config")

    with open(user_data_path, "w") as f:
        f.write("#cloud-config\n")
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
) -> dict:
    """Start an agent VM from a pre-baked image."""
    log("Checking requirements...")
    check_requirements()

    workdir = tempfile.mkdtemp(prefix="ee-agent-boot-")
    cidata_iso = create_minimal_cidata(workdir, hostname=name)

    log(f"Starting agent VM from image: {image_path}")
    ip = start_td_vm(image_path, cidata_iso, name)
    log(f"Agent VM IP: {ip}")

    log("Waiting for agent to be ready...")
    wait_for_ready(ip, port=port, timeout=300)

    log("Setting up port forwarding...")
    host_port = setup_port_forward(ip, port)

    return {
        "name": name,
        "ip": ip,
        "port": port,
        "host_port": host_port,
        "workdir": workdir,
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
    output_path: str | None = None,
    timeout: int = 1200,
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

    agent_py = (Path(__file__).parent / "agent.py").read_text()
    vm_py = Path(__file__).read_text()

    log("Creating agent bake image...")
    agent_image, cidata_iso, workdir = create_agent_image(
        base_image,
        agent_py,
        vm_py,
        vm_image_tag,
        vm_image_sha256,
        user_data_template="agent-bake-user-data.yml",
    )

    log("Starting bake VM...")
    ip = start_td_vm(agent_image, cidata_iso, name)
    log(f"Bake VM IP: {ip}")

    log("Waiting for bake VM to shut down...")
    wait_for_vm_shutdown(name, timeout=timeout)
    cleanup_vm_definition(name)

    os.makedirs(IMAGE_DIR, exist_ok=True)
    if output_path:
        dest_path = output_path
    else:
        tag = vm_image_tag or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        dest_path = os.path.join(IMAGE_DIR, f"agent-pristine-{tag}.qcow2")

    log(f"Exporting pristine image to {dest_path}...")
    subprocess.run(['qemu-img', 'convert', '-O', 'qcow2', agent_image, dest_path], check=True)

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


def setup_port_forward(vm_ip: str, vm_port: int, host_port: int = None) -> int:
    """
    Set up iptables port forwarding from host to VM.

    Args:
        vm_ip: VM's private IP address
        vm_port: Port on the VM to forward to
        host_port: Port on the host (defaults to vm_port)

    Returns:
        The host port that was configured
    """
    host_port = host_port or vm_port

    # Remove any existing rule for this port first
    subprocess.run([
        'sudo', 'iptables', '-t', 'nat', '-D', 'PREROUTING',
        '-p', 'tcp', '--dport', str(host_port),
        '-j', 'DNAT', '--to-destination', f'{vm_ip}:{vm_port}'
    ], capture_output=True)

    # Add PREROUTING rule for incoming traffic
    result = subprocess.run([
        'sudo', 'iptables', '-t', 'nat', '-A', 'PREROUTING',
        '-p', 'tcp', '--dport', str(host_port),
        '-j', 'DNAT', '--to-destination', f'{vm_ip}:{vm_port}'
    ], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to add PREROUTING rule: {result.stderr}")

    # Add OUTPUT rule so localhost traffic can reach the VM (used by SSH attestation)
    subprocess.run([
        'sudo', 'iptables', '-t', 'nat', '-D', 'OUTPUT',
        '-p', 'tcp', '-d', '127.0.0.1', '--dport', str(host_port),
        '-j', 'DNAT', '--to-destination', f'{vm_ip}:{vm_port}'
    ], capture_output=True)
    result = subprocess.run([
        'sudo', 'iptables', '-t', 'nat', '-A', 'OUTPUT',
        '-p', 'tcp', '-d', '127.0.0.1', '--dport', str(host_port),
        '-j', 'DNAT', '--to-destination', f'{vm_ip}:{vm_port}'
    ], capture_output=True, text=True)
    if result.returncode != 0:
        log(f"Warning: Failed to add OUTPUT rule: {result.stderr}")

    # Add FORWARD rule to allow the traffic
    subprocess.run([
        'sudo', 'iptables', '-D', 'FORWARD',
        '-p', 'tcp', '-d', vm_ip, '--dport', str(vm_port),
        '-j', 'ACCEPT'
    ], capture_output=True)

    result = subprocess.run([
        'sudo', 'iptables', '-A', 'FORWARD',
        '-p', 'tcp', '-d', vm_ip, '--dport', str(vm_port),
        '-j', 'ACCEPT'
    ], capture_output=True, text=True)
    if result.returncode != 0:
        log(f"Warning: Failed to add FORWARD rule: {result.stderr}")

    log(f"Port forwarding configured: *:{host_port} -> {vm_ip}:{vm_port}")
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
    parser.add_argument('--port', type=int, default=8080, help='HTTP port (default: 8080)')
    parser.add_argument('--enable-ssh', action='store_true', help='Enable SSH access (default: off)')
    parser.add_argument('--create-release', action='store_true', help='Create GitHub release with attestation')
    parser.add_argument('--endpoint', help='Endpoint URL for release (default: http://{vm_ip}:{port})')
    parser.add_argument('--agent', action='store_true', help='Create agent VM (no workload)')
    parser.add_argument('--agent-image', default='', help='Start agent VM from a pre-baked image')
    parser.add_argument('--vm-image-tag', default='', help='Agent VM image tag')
    parser.add_argument('--vm-image-sha256', default='', help='Agent VM image sha256')
    parser.add_argument('--base-image', default='', help='Base TD image path override')
    parser.add_argument('--build-pristine-agent-image', action='store_true', help='Bake a pristine agent image')
    parser.add_argument('--tdx-repo-dir', default='', help='canonical/tdx repo dir for image build')
    parser.add_argument('--tdx-repo-ref', default='main', help='canonical/tdx repo ref (default: main)')
    parser.add_argument('--tdx-guest-version', default=DEFAULT_TDX_GUEST_VERSION, help='TD guest Ubuntu version')
    parser.add_argument('--output-image', default='', help='Output path for pristine agent image')
    parser.add_argument('--bake-timeout', type=int, default=1200, help='Bake timeout seconds (default: 1200)')
    args = parser.parse_args()

    if args.build_pristine_agent_image:
        result = build_pristine_agent_image(
            name=args.name if args.name != 'ee-workload' else 'ee-agent-bake',
            vm_image_tag=args.vm_image_tag,
            vm_image_sha256=args.vm_image_sha256,
            tdx_repo_dir=args.tdx_repo_dir or None,
            tdx_repo_ref=args.tdx_repo_ref,
            tdx_guest_version=args.tdx_guest_version,
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
                port=args.port,
            )
        else:
            result = create_agent_vm(
                name=args.name,
                port=args.port,
                vm_image_tag=args.vm_image_tag,
                vm_image_sha256=args.vm_image_sha256,
                base_image=args.base_image or None,
            )
        print(json.dumps(result, indent=2))
        sys.exit(0)

    if not args.docker_compose:
        parser.error('docker_compose is required unless --agent is used')
    docker_compose = args.docker_compose

    result = create_td_vm(
        docker_compose,
        name=args.name,
        port=args.port,
        enable_ssh=args.enable_ssh,
        base_image=args.base_image or None,
    )

    if args.create_release:
        endpoint = args.endpoint or f"http://{result['ip']}:{result['port']}"
        release_url = create_release(result['quote'], endpoint)
        result['release_url'] = release_url

    # Only JSON goes to stdout, logs went to stderr
    print(json.dumps(result, indent=2))
