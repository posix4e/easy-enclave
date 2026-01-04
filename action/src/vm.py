#!/usr/bin/env python3
"""
TD VM management using Canonical TDX tooling.

Uses tdvirsh for VM management and trustauthority-cli for quote generation.
See: https://github.com/canonical/tdx
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Force unbuffered output for real-time logging
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)


def log(msg):
    """Print to stderr for logging (keeps stdout clean for JSON output)."""
    print(msg, file=sys.stderr)


TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def load_template(name: str) -> str:
    """Load a template file from the templates directory."""
    return (TEMPLATES_DIR / name).read_text()


# Default paths (Canonical TDX layout)
TDX_TOOLS_DIR = "/opt/tdx"
IMAGE_DIR = "/var/lib/easy-enclave"
DEFAULT_TD_IMAGE = f"{IMAGE_DIR}/td-guest.qcow2"

# Ubuntu cloud image URLs (TDX-compatible)
UBUNTU_CLOUD_IMAGES = {
    "24.04": "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img",
    "24.10": "https://cloud-images.ubuntu.com/oracular/current/oracular-server-cloudimg-amd64.img",
}

# Deployment state directory
DEPLOYMENTS_DIR = Path("/var/lib/easy-enclave/deployments")


def clone_repo(repo: str, ref: str = None, token: str = None) -> str:
    """Clone a GitHub repo and return path to the cloned directory.

    Args:
        repo: GitHub repo in 'owner/repo' format
        ref: Git ref (branch, tag, or commit) to checkout
        token: GitHub token for private repos

    Returns:
        Path to cloned repository
    """
    workdir = tempfile.mkdtemp(prefix="ee-deploy-")
    repo_path = os.path.join(workdir, "repo")

    # Build clone URL (with token for private repos)
    if token:
        clone_url = f"https://x-access-token:{token}@github.com/{repo}.git"
    else:
        clone_url = f"https://github.com/{repo}.git"

    log(f"Cloning {repo}...")
    result = subprocess.run(
        ['git', 'clone', '--depth', '1', clone_url, repo_path],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to clone repo: {result.stderr}")

    # Checkout specific ref if provided
    if ref:
        log(f"Checking out {ref}...")
        result = subprocess.run(
            ['git', 'checkout', ref],
            cwd=repo_path, capture_output=True, text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to checkout {ref}: {result.stderr}")

    log(f"Cloned to {repo_path}")
    return repo_path


def find_docker_compose(repo_path: str, hint: str = None) -> str:
    """Find docker-compose.yml in a cloned repo.

    Args:
        repo_path: Path to cloned repository
        hint: Optional path hint (e.g., './docker-compose.yml')

    Returns:
        Absolute path to docker-compose.yml
    """
    # If hint provided, try it first
    if hint:
        candidates = [os.path.join(repo_path, hint.lstrip('./'))]
    else:
        candidates = []

    # Standard locations to check
    candidates.extend([
        os.path.join(repo_path, "docker-compose.yml"),
        os.path.join(repo_path, "docker-compose.yaml"),
        os.path.join(repo_path, ".easyenclave", "docker-compose.yml"),
        os.path.join(repo_path, "example", "docker-compose.yml"),
    ])

    for path in candidates:
        if os.path.exists(path):
            log(f"Found docker-compose: {path}")
            return path

    raise FileNotFoundError(
        f"No docker-compose.yml found in {repo_path}. "
        f"Checked: {', '.join(candidates)}"
    )


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


def find_or_download_td_image(prefer_version: str = "24.04") -> str:
    """
    Find existing TD image or download one.

    Returns path to usable image.
    """
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


def create_workload_image(base_image: str, docker_compose_content: str, port: int = 8080, enable_ssh: bool = False) -> str:
    """
    Create a workload-specific image with docker-compose baked in.

    Returns path to the new image.
    """
    workdir = tempfile.mkdtemp(prefix="ee-workload-")
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
ssh_pwauth: true
chpasswd:
  expire: false
  users:
    - name: ubuntu
      password: ubuntu
      type: text
"""

    # Build user-data from template
    user_data = load_template("user-data.yml").format(
        ssh_config=ssh_config,
        docker_compose=indent_yaml(docker_compose_content, 6),
        start_sh=indent_yaml(start_sh, 6),
        get_quote=indent_yaml(get_quote, 6),
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

    return workload_image, cidata_iso, workdir


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

    xml_path = f"/tmp/{name}.xml"
    with open(xml_path, 'w') as f:
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
    log(f"=== Actual VM XML (interface section) ===")
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
            log(f"Last 10 lines of QEMU log:")
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
                lease_lines = [l for l in result.stdout.split('\n') if '192.168.' in l]
                if lease_lines:
                    log(f"  DHCP leases: {len(lease_lines)} found")
                    for l in lease_lines[:3]:
                        log(f"    {l.strip()}")

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


def create_td_vm(docker_compose_path: str, name: str = "ee-workload", port: int = 8080, enable_ssh: bool = False) -> dict:
    """
    Create a TD VM with the given workload.

    Returns dict with IP and quote. Raises on failure.
    """
    log("Checking requirements...")
    check_requirements()

    log(f"Finding TD base image...")
    base_image = find_td_image()
    log(f"Using base image: {base_image}")

    log(f"Reading docker-compose from {docker_compose_path}...")
    with open(docker_compose_path) as f:
        docker_compose_content = f.read()

    log("Creating workload image...")
    workload_image, cidata_iso, workdir = create_workload_image(
        base_image, docker_compose_content, port=port, enable_ssh=enable_ssh
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


def create_release(quote: str, endpoint: str, repo: str = None, token: str = None) -> str:
    """Create a GitHub release with attestation data."""
    repo = repo or os.environ.get('GITHUB_REPOSITORY')
    token = token or os.environ.get('GITHUB_TOKEN')

    if not repo or not token:
        raise ValueError("GITHUB_REPOSITORY and GITHUB_TOKEN must be set")

    now = datetime.now(timezone.utc)
    timestamp = now.strftime('%Y%m%d-%H%M%S')
    tag = f"deploy-{timestamp}"

    attestation = {
        "version": "1.0",
        "quote": quote,
        "endpoint": endpoint,
        "timestamp": now.isoformat().replace('+00:00', 'Z'),
        "repo": repo
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

    subprocess.run(
        ['gh', 'release', 'create', tag, '--repo', repo, '--title', f'Deployment {timestamp}', '--notes', body],
        check=True, capture_output=True, env={**os.environ, 'GITHUB_TOKEN': token}
    )
    log(f"Created release: {tag}")

    attestation_file = '/tmp/attestation.json'
    with open(attestation_file, 'w') as f:
        json.dump(attestation, f, indent=2)

    subprocess.run(
        ['gh', 'release', 'upload', tag, attestation_file, '--repo', repo],
        check=True, capture_output=True, env={**os.environ, 'GITHUB_TOKEN': token}
    )
    log("Uploaded attestation.json")

    return f"https://github.com/{repo}/releases/tag/{tag}"


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Create TD VM with workload')
    parser.add_argument('docker_compose', nargs='?', help='Path to docker-compose.yml (optional if --repo is used)')
    parser.add_argument('--repo', help='GitHub repo to clone (owner/repo format)')
    parser.add_argument('--ref', help='Git ref to checkout (branch/tag/commit)')
    parser.add_argument('--name', default='ee-workload', help='VM name (default: ee-workload)')
    parser.add_argument('--port', type=int, default=8080, help='HTTP port (default: 8080)')
    parser.add_argument('--enable-ssh', action='store_true', help='Enable SSH access (default: off)')
    parser.add_argument('--create-release', action='store_true', help='Create GitHub release with attestation')
    parser.add_argument('--endpoint', help='Endpoint URL for release (default: http://{vm_ip}:{port})')
    args = parser.parse_args()

    # Determine docker-compose path
    if args.repo:
        # Clone repo and find docker-compose
        token = os.environ.get('GITHUB_TOKEN')
        repo_path = clone_repo(args.repo, ref=args.ref, token=token)
        docker_compose = find_docker_compose(repo_path, hint=args.docker_compose)
    elif args.docker_compose:
        docker_compose = args.docker_compose
    else:
        parser.error('Either docker_compose path or --repo is required')

    result = create_td_vm(docker_compose, name=args.name, port=args.port, enable_ssh=args.enable_ssh)

    # Add repo info to result if cloning was used
    if args.repo:
        result['repo'] = args.repo
        result['ref'] = args.ref

    if args.create_release:
        endpoint = args.endpoint or f"http://{result['ip']}:{result['port']}"
        release_url = create_release(result['quote'], endpoint)
        result['release_url'] = release_url

    # Only JSON goes to stdout, logs went to stderr
    print(json.dumps(result, indent=2))
