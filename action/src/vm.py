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
import hashlib
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

# Force unbuffered output for real-time logging
sys.stdout.reconfigure(line_buffering=True)


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

    # Create overlay image (don't specify size - inherit from base)
    subprocess.run([
        'qemu-img', 'create', '-f', 'qcow2',
        '-b', base_image, '-F', 'qcow2',
        workload_image
    ], check=True, capture_output=True)

    # Create cloud-init files
    user_data = f"""#cloud-config
hostname: ee-workload

# Enable password auth for SSH (for debugging)
ssh_pwauth: true
chpasswd:
  expire: false
  users:
    - name: ubuntu
      password: ubuntu
      type: text

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

      # Generate TDX quote first (stderr goes to log, stdout to JSON)
      python3 /opt/workload/get-quote.py > /opt/workload/quote.json 2>/opt/workload/quote.log || echo '{{"success": false, "error": "quote generation failed"}}' > /opt/workload/quote.json
      chmod 644 /opt/workload/quote.json /opt/workload/quote.log 2>/dev/null || true

      # Create response HTML
      cat > /opt/workload/index.html << 'HTMLEOF'
      <!DOCTYPE html>
      <html>
      <head><title>TDX Attested Enclave</title></head>
      <body>
      <h1>Hello from TDX-attested enclave!</h1>
      <p>This service is running inside an Intel TDX Trust Domain.</p>
      </body>
      </html>
      HTMLEOF

      # Start simple HTTP server on port 8080
      cd /opt/workload
      python3 -m http.server 8080 &

      # Signal ready
      touch /opt/workload/ready

  - path: /opt/workload/get-quote.py
    permissions: '0755'
    content: |
      #!/usr/bin/env python3
      import base64
      import json
      import os
      import subprocess
      import sys

      def debug(msg):
          print(f"DEBUG: {{msg}}", file=sys.stderr)

      def get_tdx_quote():
          # Debug: show available TDX interfaces
          debug(f"Checking /dev/tdx_guest: {{os.path.exists('/dev/tdx_guest')}}")
          if os.path.exists('/dev/tdx_guest'):
              debug(f"  perms: {{oct(os.stat('/dev/tdx_guest').st_mode)}}")
          debug(f"Checking /sys/kernel/config/tsm/report: {{os.path.exists('/sys/kernel/config/tsm/report')}}")
          if os.path.exists('/sys/kernel/config/tsm/report'):
              try:
                  contents = os.listdir('/sys/kernel/config/tsm/report')
                  debug(f"  contents: {{contents}}")
              except Exception as e:
                  debug(f"  listdir failed: {{e}}")

          # Try configfs-tsm first (kernel 6.7+)
          try:
              tsm_path = "/sys/kernel/config/tsm/report"
              if os.path.exists(tsm_path):
                  import tempfile
                  import time
                  report_dir = tempfile.mkdtemp(dir=tsm_path)
                  debug(f"Created report dir: {{report_dir}}")
                  inblob = os.path.join(report_dir, "inblob")
                  outblob = os.path.join(report_dir, "outblob")
                  with open(inblob, 'wb') as f:
                      f.write(b'\\x00' * 64)
                  debug(f"Wrote inblob, reading outblob...")
                  time.sleep(1)  # Give QGS time to respond
                  with open(outblob, 'rb') as f:
                      data = f.read()
                  debug(f"Read {{len(data)}} bytes from outblob")
                  if len(data) > 0:
                      return data
                  debug("outblob was empty")
          except Exception as e:
              debug(f"configfs-tsm failed: {{e}}")

          # Try /dev/tdx_guest
          try:
              import fcntl
              TDX_CMD_GET_REPORT = 0xc0104401
              buf = bytearray(b'\\x00' * 64 + b'\\x00' * 1024)
              with open('/dev/tdx_guest', 'rb+', buffering=0) as f:
                  fcntl.ioctl(f, TDX_CMD_GET_REPORT, buf)
              debug(f"Got {{len(buf)}} bytes from /dev/tdx_guest")
              return bytes(buf[64:])
          except Exception as e:
              debug(f"/dev/tdx_guest failed: {{e}}")

          return None

      quote = get_tdx_quote()
      if quote and len(quote) > 100:
          print(json.dumps({{
              "success": True,
              "quote": base64.b64encode(quote).decode(),
              "size": len(quote)
          }}))
      elif quote:
          print(json.dumps({{"success": False, "error": f"Quote too small ({{len(quote)}} bytes)", "size": len(quote)}}))
      else:
          print(json.dumps({{"success": False, "error": "No quote mechanism available"}}))

runcmd:
  - /opt/workload/start.sh
"""

    user_data_path = os.path.join(workdir, "user-data")
    meta_data_path = os.path.join(workdir, "meta-data")
    network_config_path = os.path.join(workdir, "network-config")

    # Network config to enable DHCP
    network_config = """version: 2
ethernets:
  id0:
    match:
      driver: virtio*
    dhcp4: true
"""

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
    print(f"Cleaning up existing VM {name}...")
    subprocess.run(['sudo', 'virsh', 'destroy', name], capture_output=True)
    subprocess.run(['sudo', 'virsh', 'undefine', name, '--nvram'], capture_output=True)

    # Wait a moment for cleanup
    time.sleep(1)

    # Verify cleanup
    check = subprocess.run(['sudo', 'virsh', 'domstate', name], capture_output=True, text=True)
    if check.returncode == 0:
        print(f"Warning: VM {name} still exists, forcing undefine...")
        subprocess.run(['sudo', 'virsh', 'undefine', name, '--nvram', '--remove-all-storage'], capture_output=True)
        time.sleep(1)

    # Define and start
    result = subprocess.run(['sudo', 'virsh', 'define', xml_path], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"virsh define failed: {result.stderr}")
        raise RuntimeError(f"Failed to define VM: {result.stderr}")

    result = subprocess.run(['sudo', 'virsh', 'start', name], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"virsh start failed: {result.stderr}")
        raise RuntimeError(f"Failed to start VM: {result.stderr}")

    print(f"VM {name} started successfully")

    # Give VM a moment to boot
    time.sleep(10)

    # Check VM state
    result = subprocess.run(['sudo', 'virsh', 'domstate', name], capture_output=True, text=True)
    print(f"VM state: {result.stdout.strip()}")

    # Dump actual XML to see what libvirt created
    result = subprocess.run(['sudo', 'virsh', 'dumpxml', name], capture_output=True, text=True)
    print(f"=== Actual VM XML (interface section) ===")
    for line in result.stdout.split('\n'):
        if 'interface' in line.lower() or 'source network' in line.lower() or 'model type' in line.lower() or 'mac address' in line.lower():
            print(line)

    # Check network bridge
    result = subprocess.run(['ip', 'link', 'show', 'virbr0'], capture_output=True, text=True)
    print(f"virbr0 status: {result.stdout.strip() if result.returncode == 0 else 'not found'}")

    # Check DHCP leases
    result = subprocess.run(['sudo', 'virsh', 'net-dhcp-leases', 'default'], capture_output=True, text=True)
    print(f"DHCP leases:\n{result.stdout}")

    # Check ARP table for any new entries (use ip neigh instead of arp)
    result = subprocess.run(['ip', 'neigh'], capture_output=True, text=True)
    print(f"ARP/Neighbor table:\n{result.stdout}")

    # Try to get console log to see boot status
    print("=== Checking VM console/serial log ===")
    try:
        # Check qemu log if available
        result = subprocess.run(['sudo', 'cat', f'/var/log/libvirt/qemu/{name}.log'],
                               capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            lines = result.stdout.strip().split('\n')
            print(f"Last 10 lines of QEMU log:")
            for line in lines[-10:]:
                print(f"  {line}")
    except Exception as e:
        print(f"Could not read QEMU log: {e}")

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
    # Find OVMF firmware - use same path as Canonical
    ovmf_paths = [
        "/usr/share/qemu/OVMF.fd",
        "/usr/share/OVMF/OVMF_CODE_4M.fd",
        "/usr/share/OVMF/OVMF_CODE.fd",
    ]
    ovmf = next((p for p in ovmf_paths if os.path.exists(p)), ovmf_paths[0])

    # Note: Using type='rom' instead of 'pflash' - this is key for TDX!
    return f"""<domain type='kvm' xmlns:qemu='http://libvirt.org/schemas/domain/qemu/1.0'>
  <name>{name}</name>
  <memory unit='MiB'>{memory_mb}</memory>
  <memoryBacking>
    <source type="anonymous"/>
    <access mode="private"/>
  </memoryBacking>
  <vcpu placement='static'>{vcpus}</vcpu>

  <os>
    <type arch='x86_64' machine='q35'>hvm</type>
    <loader type='rom' readonly='yes'>{ovmf}</loader>
    <boot dev='hd'/>
  </os>

  <features>
    <acpi/>
    <apic/>
    <ioapic driver='qemu'/>
  </features>

  <clock offset='utc'>
    <timer name='hpet' present='no'/>
  </clock>

  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>destroy</on_crash>

  <pm>
    <suspend-to-mem enable='no'/>
    <suspend-to-disk enable='no'/>
  </pm>

  <cpu mode='host-passthrough'>
    <topology sockets='1' cores='{vcpus}' threads='1'/>
  </cpu>

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
      <address type='pci' domain='0x0000' bus='0x06' slot='0x00' function='0x0'/>
    </interface>

    <serial type='file'>
      <source path='/var/log/libvirt/qemu/{name}-serial.log'/>
      <target type='isa-serial' port='0'>
        <model name='isa-serial'/>
      </target>
    </serial>
    <console type='file'>
      <source path='/var/log/libvirt/qemu/{name}-serial.log'/>
      <target type='serial' port='0'/>
    </console>

    <channel type='unix'>
      <source mode='bind'/>
      <target type='virtio' name='org.qemu.guest_agent.0'/>
    </channel>

    <vsock model='virtio'>
      <cid auto='yes'/>
      <address type='pci' domain='0x0000' bus='0x05' slot='0x00' function='0x0'/>
    </vsock>
  </devices>

  <allowReboot value='no'/>

  <launchSecurity type='tdx'>
    <policy>0x10000000</policy>
    <quoteGenerationService>
      <SocketAddress type='unix' path='/var/run/tdx-qgs/qgs.socket'/>
    </quoteGenerationService>
  </launchSecurity>
</domain>
"""


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
        print(f"VM MAC address: {vm_mac}")
    else:
        print("Warning: Could not get VM MAC address")

    while time.time() - start < timeout:
        elapsed = int(time.time() - start)
        if elapsed - last_print >= 30:
            last_print = elapsed
            print(f"Waiting for VM IP... ({elapsed}s elapsed)")
            # Show DHCP leases periodically
            result = subprocess.run(['sudo', 'virsh', 'net-dhcp-leases', 'default'],
                                   capture_output=True, text=True)
            if result.stdout.strip():
                lease_lines = [l for l in result.stdout.split('\n') if '192.168.' in l]
                if lease_lines:
                    print(f"  DHCP leases: {len(lease_lines)} found")
                    for l in lease_lines[:3]:
                        print(f"    {l.strip()}")

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

        # Try virsh net-dhcp-leases - match by VM name or MAC address
        try:
            result = subprocess.run(
                ['sudo', 'virsh', 'net-dhcp-leases', 'default'],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.split('\n'):
                line_lower = line.lower()
                # Match by hostname or MAC address
                if name.lower() in line_lower or (vm_mac and vm_mac in line_lower):
                    parts = line.split()
                    for part in parts:
                        if '/' in part and '.' in part and part.startswith('192.'):
                            ip = part.split('/')[0]
                            print(f"Found IP {ip} for VM {name} (MAC: {vm_mac})")
                            return ip
        except Exception:
            pass

        time.sleep(10)

    raise TimeoutError(f"VM {name} did not get IP within {timeout}s")


def wait_for_ready(ip: str, timeout: int = 300) -> None:
    """Wait for workload to be ready by checking port 8080."""
    import socket
    start = time.time()
    last_print = 0
    while time.time() - start < timeout:
        elapsed = int(time.time() - start)
        if elapsed - last_print >= 30:
            last_print = elapsed
            print(f"Waiting for workload on port 8080... ({elapsed}s elapsed)")

        try:
            # Try to connect to port 8080
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((ip, 8080))
            sock.close()
            if result == 0:
                print(f"Port 8080 is open on {ip}")
                # Give it a moment to fully start
                time.sleep(2)
                return
        except Exception:
            pass
        time.sleep(5)

    raise TimeoutError(f"Workload not ready within {timeout}s")


def get_quote_from_vm(ip: str) -> str:
    """Retrieve quote from VM. Returns base64-encoded quote."""
    # First, get the debug log
    log_result = subprocess.run([
        'sshpass', '-p', 'ubuntu',
        'ssh', '-o', 'StrictHostKeyChecking=no',
        '-o', 'UserKnownHostsFile=/dev/null',
        '-o', 'ConnectTimeout=10',
        f'ubuntu@{ip}', 'cat /opt/workload/quote.log 2>/dev/null || echo "No log file"'
    ], capture_output=True, text=True, timeout=30)
    if log_result.stdout.strip():
        print(f"=== Quote generation debug log ===")
        print(log_result.stdout)
        print(f"=== End debug log ===")

    # Use sshpass for password auth
    result = subprocess.run([
        'sshpass', '-p', 'ubuntu',
        'ssh', '-o', 'StrictHostKeyChecking=no',
        '-o', 'UserKnownHostsFile=/dev/null',
        '-o', 'ConnectTimeout=10',
        f'ubuntu@{ip}', 'cat /opt/workload/quote.json'
    ], capture_output=True, text=True, timeout=30)

    if result.returncode != 0:
        # Try without sshpass in case it's not installed
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
