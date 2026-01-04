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
from pathlib import Path
from typing import Optional


# Default paths (Canonical TDX layout)
TDX_TOOLS_DIR = "/opt/tdx"  # or wherever canonical/tdx is cloned
DEFAULT_TD_IMAGE = "/var/lib/easy-enclave/td-guest.qcow2"
BACKUP_TD_IMAGE = "/home/ubuntu/tdx/guest-tools/image/tdx-guest-ubuntu-24.04.qcow2"


def find_td_image() -> str:
    """Find the TD guest image."""
    candidates = [
        DEFAULT_TD_IMAGE,
        BACKUP_TD_IMAGE,
        "/var/lib/libvirt/images/tdx-guest.qcow2",
        os.path.expanduser("~/tdx/guest-tools/image/tdx-guest-ubuntu-24.04.qcow2"),
        os.path.expanduser("~/tdx/guest-tools/image/tdx-guest-ubuntu-25.04.qcow2"),
    ]

    for path in candidates:
        if os.path.exists(path):
            return path

    raise RuntimeError(
        f"TD guest image not found. Tried: {candidates}\n"
        "Run: cd ~/tdx/guest-tools/image && sudo ./create-td-image.sh"
    )


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
    subprocess.run(['virsh', 'destroy', name], capture_output=True)
    subprocess.run(['virsh', 'undefine', name], capture_output=True)

    # Define and start
    result = subprocess.run(['virsh', 'define', xml_path], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"virsh define failed: {result.stderr}")
        raise RuntimeError(f"Failed to define VM: {result.stderr}")

    result = subprocess.run(['virsh', 'start', name], capture_output=True, text=True)
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
    # Find OVMF firmware
    ovmf_paths = [
        "/usr/share/OVMF/OVMF_CODE_4M.fd",
        "/usr/share/OVMF/OVMF_CODE.fd",
        "/usr/share/qemu/OVMF_CODE.fd",
    ]
    ovmf = next((p for p in ovmf_paths if os.path.exists(p)), ovmf_paths[0])

    return f"""<domain type='kvm' xmlns:qemu='http://libvirt.org/schemas/domain/qemu/1.0'>
  <name>{name}</name>
  <memory unit='MiB'>{memory_mb}</memory>
  <vcpu placement='static'>{vcpus}</vcpu>

  <os>
    <type arch='x86_64' machine='q35'>hvm</type>
    <loader readonly='yes' type='pflash'>{ovmf}</loader>
    <boot dev='hd'/>
  </os>

  <features>
    <acpi/>
    <apic/>
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
                ['virsh', 'domifaddr', name, '--source', 'agent'],
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
                ['virsh', 'domifaddr', name],
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

    print(f"Warning: Workload not ready within {timeout}s, continuing anyway")


def get_quote_from_vm(ip: str) -> dict:
    """Retrieve quote from VM."""
    try:
        result = subprocess.run([
            'ssh', '-o', 'StrictHostKeyChecking=no',
            '-o', 'UserKnownHostsFile=/dev/null',
            '-o', 'ConnectTimeout=10',
            f'ubuntu@{ip}', 'cat /opt/workload/quote.json'
        ], capture_output=True, text=True, timeout=30)

        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except Exception as e:
        print(f"Error getting quote: {e}")

    return {"success": False, "error": "Failed to retrieve quote"}


def create_td_vm(docker_compose_path: str, name: str = "ee-workload") -> dict:
    """
    Create a TD VM with the given workload.

    Returns dict with IP, quote, and status.
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
    quote_data = get_quote_from_vm(ip)

    return {
        "name": name,
        "ip": ip,
        "quote": quote_data.get("quote", ""),
        "success": quote_data.get("success", False),
        "workdir": workdir,
    }


def destroy_td_vm(name: str = "ee-workload") -> None:
    """Destroy a TD VM."""
    subprocess.run(['virsh', 'destroy', name], capture_output=True)
    subprocess.run(['virsh', 'undefine', name], capture_output=True)


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: vm.py <docker-compose.yml>")
        sys.exit(1)

    result = create_td_vm(sys.argv[1])
    print(json.dumps(result, indent=2))
