#!/usr/bin/env python3
"""
TD VM management using Canonical TDX tooling.

Uses tdvirsh for VM management and trustauthority-cli for quote generation.
See: https://github.com/canonical/tdx
"""

import json
import os
import subprocess
import tempfile
import time
import hashlib
import urllib.request
from pathlib import Path
from typing import Optional, Tuple


# Default paths (Canonical TDX layout)
TDX_TOOLS_DIR = "/opt/tdx"
IMAGE_DIR = "/var/lib/easy-enclave"
DEFAULT_TD_IMAGE = f"{IMAGE_DIR}/td-guest.qcow2"

# Ubuntu cloud image URLs (TDX-compatible)
UBUNTU_CLOUD_IMAGES = {
    "24.04": "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img",
    "24.10": "https://cloud-images.ubuntu.com/oracular/current/oracular-server-cloudimg-amd64.img",
}


def get_tdx_info() -> dict:
    """Get TDX information from the host."""
    info = {
        "tdx_available": False,
        "tdx_version": None,
        "kernel": None,
        "libvirt_tdx": False,
    }

    # Check kernel
    try:
        result = subprocess.run(['uname', '-r'], capture_output=True, text=True)
        info["kernel"] = result.stdout.strip()
    except Exception:
        pass

    # Check for TDX in dmesg
    try:
        result = subprocess.run(['dmesg'], capture_output=True, text=True)
        if 'tdx' in result.stdout.lower():
            info["tdx_available"] = True
            # Try to find version
            for line in result.stdout.split('\n'):
                if 'tdx' in line.lower() and 'version' in line.lower():
                    info["tdx_version"] = line.strip()
                    break
    except Exception:
        pass

    # Check /sys for TDX
    tdx_paths = [
        "/sys/firmware/tdx",
        "/sys/module/kvm_intel/parameters/tdx",
    ]
    for path in tdx_paths:
        if os.path.exists(path):
            info["tdx_available"] = True
            try:
                with open(path) as f:
                    info["tdx_version"] = f.read().strip()
            except Exception:
                pass
            break

    # Check libvirt TDX support
    try:
        result = subprocess.run(
            ['virsh', 'domcapabilities', '--machine', 'q35'],
            capture_output=True, text=True
        )
        if 'tdx' in result.stdout.lower():
            info["libvirt_tdx"] = True
    except Exception:
        pass

    return info


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
        print(f"Image already exists: {dest_path}")
        return dest_path

    print(f"Downloading Ubuntu {version} cloud image...")
    print(f"URL: {url}")
    print(f"Destination: {dest_path}")

    # Download with progress
    def reporthook(count, block_size, total_size):
        percent = int(count * block_size * 100 / total_size)
        print(f"\rProgress: {percent}%", end='', flush=True)

    urllib.request.urlretrieve(url, dest_path, reporthook)
    print("\nDownload complete!")

    # Convert to qcow2 if needed
    if dest_path.endswith('.img'):
        qcow2_path = dest_path.replace('.img', '.qcow2')
        print(f"Converting to qcow2: {qcow2_path}")
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
            print(f"Found TD image: {img['path']} ({img['size_gb']} GB)")
            return img['path']

    # Then look for any cloud image
    for img in existing:
        if 'cloud' in img['name'].lower() or 'ubuntu' in img['name'].lower():
            print(f"Found cloud image: {img['path']} ({img['size_gb']} GB)")
            return img['path']

    # If any qcow2/img exists, use the largest one
    if existing:
        largest = max(existing, key=lambda x: x['size_gb'])
        print(f"Using existing image: {largest['path']} ({largest['size_gb']} GB)")
        return largest['path']

    # No images found - download
    print("No existing images found. Downloading Ubuntu cloud image...")
    return download_ubuntu_image(prefer_version)


def find_td_image() -> str:
    """Find the TD guest image (legacy function, now uses find_or_download)."""
    return find_or_download_td_image()


def create_workload_image(base_image: str, docker_compose_content: str) -> str:
    """
    Create a workload-specific image with docker-compose baked in.

    Returns path to the new image.
    """
    workdir = tempfile.mkdtemp(prefix="ee-workload-")
    workload_image = os.path.join(workdir, "workload.qcow2")

    # Create overlay image
    subprocess.run([
        'qemu-img', 'create', '-f', 'qcow2',
        '-b', base_image, '-F', 'qcow2',
        workload_image, '20G'
    ], check=True, capture_output=True)

    # Create cloud-init files
    user_data = f"""#cloud-config
hostname: ee-workload

write_files:
  - path: /opt/workload/docker-compose.yml
    content: |
{indent_yaml(docker_compose_content, 6)}

  - path: /opt/workload/start.sh
    permissions: '0755'
    content: |
      #!/bin/bash
      set -e
      cd /opt/workload

      # Wait for docker
      while ! docker info &>/dev/null; do sleep 1; done

      # Start workload
      docker compose up -d

      # Generate quote using trustauthority-cli if available
      if command -v trustauthority-cli &>/dev/null; then
        trustauthority-cli evidence --tdx > /opt/workload/evidence.json 2>/dev/null || true
      fi

      # Also try direct /dev/tdx_guest
      python3 /opt/workload/get-quote.py > /opt/workload/quote.json 2>/dev/null || true

      # Signal ready
      touch /opt/workload/ready

  - path: /opt/workload/get-quote.py
    permissions: '0755'
    content: |
      #!/usr/bin/env python3
      import base64
      import json
      import os

      def get_tdx_quote():
          try:
              # Try configfs-tsm first (kernel 6.7+)
              tsm_path = "/sys/kernel/config/tsm/report"
              if os.path.exists(tsm_path):
                  import tempfile
                  report_dir = tempfile.mkdtemp(dir=tsm_path)
                  inblob = os.path.join(report_dir, "inblob")
                  outblob = os.path.join(report_dir, "outblob")
                  with open(inblob, 'wb') as f:
                      f.write(b'\\x00' * 64)
                  with open(outblob, 'rb') as f:
                      return f.read()
          except Exception as e:
              print(f"configfs-tsm failed: {{e}}")

          try:
              # Try /dev/tdx_guest
              import fcntl
              TDX_CMD_GET_REPORT = 0xc0104401
              buf = bytearray(b'\\x00' * 64 + b'\\x00' * 1024)
              with open('/dev/tdx_guest', 'rb+', buffering=0) as f:
                  fcntl.ioctl(f, TDX_CMD_GET_REPORT, buf)
              return bytes(buf[64:])
          except Exception as e:
              print(f"/dev/tdx_guest failed: {{e}}")

          return None

      quote = get_tdx_quote()
      if quote:
          print(json.dumps({{
              "success": True,
              "quote": base64.b64encode(quote).decode(),
              "size": len(quote)
          }}))
      else:
          print(json.dumps({{"success": False, "error": "No quote mechanism available"}}))

runcmd:
  - /opt/workload/start.sh
"""

    user_data_path = os.path.join(workdir, "user-data")
    meta_data_path = os.path.join(workdir, "meta-data")

    with open(user_data_path, 'w') as f:
        f.write(user_data)
    with open(meta_data_path, 'w') as f:
        f.write("instance-id: ee-workload\nlocal-hostname: ee-workload\n")

    # Create cloud-init ISO
    cidata_iso = os.path.join(workdir, "cidata.iso")
    subprocess.run([
        'genisoimage', '-output', cidata_iso,
        '-volid', 'cidata', '-joliet', '-rock',
        user_data_path, meta_data_path
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

    # Destroy existing VM
    subprocess.run(['sudo', 'virsh', 'destroy', name], capture_output=True)
    subprocess.run(['sudo', 'virsh', 'undefine', name], capture_output=True)

    # Define and start
    result = subprocess.run(['sudo', 'virsh', 'define', xml_path], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"virsh define failed: {result.stderr}")
        raise RuntimeError(f"Failed to define VM: {result.stderr}")

    result = subprocess.run(['sudo', 'virsh', 'start', name], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"virsh start failed: {result.stderr}")
        raise RuntimeError(f"Failed to start VM: {result.stderr}")

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
    """Generate libvirt XML for TDX VM."""
    # Find OVMF firmware - prefer TDX-specific builds
    ovmf_code_paths = [
        "/usr/share/OVMF/OVMF_CODE_4M.ms.fd",  # TDX-compatible
        "/usr/share/OVMF/OVMF_CODE.ms.fd",
        "/usr/share/OVMF/OVMF_CODE_4M.fd",
        "/usr/share/OVMF/OVMF_CODE.fd",
        "/usr/share/qemu/OVMF_CODE.fd",
    ]
    ovmf_vars_paths = [
        "/usr/share/OVMF/OVMF_VARS_4M.ms.fd",
        "/usr/share/OVMF/OVMF_VARS.ms.fd",
        "/usr/share/OVMF/OVMF_VARS_4M.fd",
        "/usr/share/OVMF/OVMF_VARS.fd",
    ]
    ovmf_code = next((p for p in ovmf_code_paths if os.path.exists(p)), ovmf_code_paths[0])
    ovmf_vars = next((p for p in ovmf_vars_paths if os.path.exists(p)), None)

    # For TDX, we need both CODE and VARS, and VARS should be a copy (not template)
    nvram_section = ""
    if ovmf_vars:
        nvram_path = f"/tmp/{name}-VARS.fd"
        # Copy the template to create a writeable NVRAM
        subprocess.run(['cp', ovmf_vars, nvram_path], check=True)
        nvram_section = f"<nvram template='{ovmf_vars}'>{nvram_path}</nvram>"

    return f"""<domain type='kvm'>
  <name>{name}</name>
  <memory unit='MiB'>{memory_mb}</memory>
  <vcpu placement='static'>{vcpus}</vcpu>

  <os>
    <type arch='x86_64' machine='q35'>hvm</type>
    <loader readonly='yes' type='pflash'>{ovmf_code}</loader>
    {nvram_section}
    <boot dev='hd'/>
  </os>

  <features>
    <acpi/>
    <apic/>
    <ioapic driver='qemu'/>
  </features>

  <cpu mode='host-passthrough'>
  </cpu>

  <clock offset='utc'/>

  <launchSecurity type='tdx'>
    <policy>0x0</policy>
  </launchSecurity>

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

    <serial type='pty'>
      <target port='0'/>
    </serial>
    <console type='pty'>
      <target type='serial' port='0'/>
    </console>
  </devices>
</domain>
"""


def wait_for_vm_ip(name: str, timeout: int = 120) -> str:
    """Wait for VM to get an IP address."""
    start = time.time()
    while time.time() - start < timeout:
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

        # Also try lease file
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

        time.sleep(5)

    raise TimeoutError(f"VM {name} did not get IP within {timeout}s")


def wait_for_ready(ip: str, timeout: int = 300) -> None:
    """Wait for workload to signal ready."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            result = subprocess.run([
                'ssh', '-o', 'StrictHostKeyChecking=no',
                '-o', 'UserKnownHostsFile=/dev/null',
                '-o', 'ConnectTimeout=5',
                '-o', 'BatchMode=yes',
                f'ubuntu@{ip}', 'test -f /opt/workload/ready && echo ready'
            ], capture_output=True, text=True, timeout=15)

            if 'ready' in result.stdout:
                return
        except Exception:
            pass
        time.sleep(10)

    raise TimeoutError(f"Workload not ready within {timeout}s")


def get_quote_from_vm(ip: str) -> str:
    """Retrieve quote from VM. Returns base64-encoded quote."""
    result = subprocess.run([
        'ssh', '-o', 'StrictHostKeyChecking=no',
        '-o', 'UserKnownHostsFile=/dev/null',
        '-o', 'ConnectTimeout=10',
        f'ubuntu@{ip}', 'cat /opt/workload/quote.json'
    ], capture_output=True, text=True, timeout=30)

    if result.returncode != 0:
        raise RuntimeError(f"Failed to read quote from VM: {result.stderr}")

    if not result.stdout.strip():
        raise RuntimeError("Empty quote response from VM")

    data = json.loads(result.stdout)
    if not data.get("success"):
        raise RuntimeError(f"Quote generation failed in VM: {data.get('error', 'unknown')}")

    if not data.get("quote"):
        raise RuntimeError("No quote in VM response")

    return data["quote"]


def create_td_vm(docker_compose_path: str, name: str = "ee-workload") -> dict:
    """
    Create a TD VM with the given workload.

    Returns dict with IP and quote. Raises on failure.
    """
    print(f"Finding TD base image...")
    base_image = find_td_image()
    print(f"Using base image: {base_image}")

    print(f"Reading docker-compose from {docker_compose_path}...")
    with open(docker_compose_path) as f:
        docker_compose_content = f.read()

    print("Creating workload image...")
    workload_image, cidata_iso, workdir = create_workload_image(
        base_image, docker_compose_content
    )

    print("Starting TD VM...")
    ip = start_td_vm(workload_image, cidata_iso, name)
    print(f"VM IP: {ip}")

    print("Waiting for workload...")
    wait_for_ready(ip, timeout=300)

    print("Retrieving quote...")
    quote = get_quote_from_vm(ip)

    return {
        "name": name,
        "ip": ip,
        "quote": quote,
        "workdir": workdir,
    }


def destroy_td_vm(name: str = "ee-workload") -> None:
    """Destroy a TD VM."""
    subprocess.run(['sudo', 'virsh', 'destroy', name], capture_output=True)
    subprocess.run(['sudo', 'virsh', 'undefine', name], capture_output=True)


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: vm.py <docker-compose.yml>")
        sys.exit(1)

    result = create_td_vm(sys.argv[1])
    print(json.dumps(result, indent=2))
