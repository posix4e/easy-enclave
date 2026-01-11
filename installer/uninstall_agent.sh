#!/usr/bin/env bash
set -euo pipefail

VM_NAME="ee-agent"
VM_PORT="8000"
HOST_PORT=""
PUBLIC_IP=""

usage() {
  cat <<'USAGE'
uninstall_agent.sh

Usage: sudo ./installer/uninstall_agent.sh [options]

Options:
  --vm-name NAME      VM name to destroy/undefine (default: ee-agent)
  --vm-port PORT      VM port inside the VM (default: 8000)
  --host-port PORT    Host port forwarded to the VM (optional, used for cleanup)
  --public-ip IP      Public IP mapped to this VM (optional, used for cleanup)
  -h, --help          Show this help
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --vm-name) VM_NAME="$2"; shift 2;;
    --vm-port) VM_PORT="$2"; shift 2;;
    --host-port) HOST_PORT="$2"; shift 2;;
    --public-ip) PUBLIC_IP="$2"; shift 2;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown option: $1" >&2; usage; exit 1;;
  esac
done

if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (sudo)" >&2
  exit 1
fi

log() { printf '%s\n' "$*" >&2; }

cleanup_vm() {
  local name="$1"
  virsh destroy "$name" >/dev/null 2>&1 || true
  virsh undefine "$name" --nvram --managed-save --remove-all-storage >/dev/null 2>&1 || \
    virsh undefine "$name" --nvram --remove-all-storage >/dev/null 2>&1 || true
}

delete_nat_rules() {
  local host_port="$1"
  local ip="$2"
  [ -z "$host_port" ] && return 0
  for chain in PREROUTING OUTPUT; do
    local lines
    lines=$(iptables -t nat -L "$chain" --line-numbers | awk -v port="dpt:${host_port}" -v ip="$ip" '
      $1 ~ /^[0-9]+$/ && $0 ~ port && (ip == "" || $0 ~ ip) {print $1}' | sort -rn)
    for num in $lines; do
      iptables -t nat -D "$chain" "$num" || true
    done
  done
}

delete_forward_rules() {
  local vm_port="$1"
  [ -z "$vm_port" ] && return 0
  for chain in FORWARD LIBVIRT_FWI; do
    local lines
    lines=$(iptables -L "$chain" --line-numbers | awk -v port="dpt:${vm_port}" '$1 ~ /^[0-9]+$/ && $0 ~ port {print $1}' | sort -rn)
    for num in $lines; do
      iptables -D "$chain" "$num" || true
    done
  done
}

log "Uninstalling VM ${VM_NAME}"
cleanup_vm "$VM_NAME"
delete_nat_rules "$HOST_PORT" "$PUBLIC_IP"
delete_forward_rules "$VM_PORT"
log "Uninstall complete for ${VM_NAME}"
