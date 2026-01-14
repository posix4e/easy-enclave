#!/usr/bin/env bash
set -euo pipefail

VM_NAME="ee-agent"
VM_PORT="8000"
HOST_PORT=""
PUBLIC_PORT=""
PUBLIC_IP=""

usage() {
  cat <<'USAGE'
uninstall_agent.sh

Usage: sudo ./installer/uninstall_agent.sh [options]

Options:
  --vm-name NAME      VM name to destroy/undefine (default: ee-agent)
  --vm-port PORT      VM port inside the VM (default: 8000)
  --host-port PORT    Host port forwarded to the VM (optional, used for cleanup)
  --public-port PORT  VM port exposed publicly (default: 443)
  --public-ip IP      Public IP mapped to this VM (optional, used for cleanup)
  -h, --help          Show this help
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --vm-name) VM_NAME="$2"; shift 2;;
    --vm-port) VM_PORT="$2"; shift 2;;
    --host-port) HOST_PORT="$2"; shift 2;;
    --public-port) PUBLIC_PORT="$2"; shift 2;;
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

get_vm_ip() {
  local name="$1"
  local ip=""
  ip=$(virsh domifaddr "$name" 2>/dev/null | awk '/ipv4/ {print $4}' | cut -d/ -f1 | head -n1)
  if [ -n "$ip" ]; then
    printf '%s\n' "$ip"
    return 0
  fi
  local mac=""
  mac=$(virsh domiflist "$name" 2>/dev/null | awk 'NR>2 && $5 ~ /:/ {print $5; exit}')
  if [ -n "$mac" ]; then
    ip=$(virsh net-dhcp-leases default 2>/dev/null | awk -v mac="$mac" '$0 ~ mac {print $5; exit}' | cut -d/ -f1)
    if [ -n "$ip" ]; then
      printf '%s\n' "$ip"
      return 0
    fi
  fi
  return 1
}

get_bridge_info() {
  local route_line=""
  route_line=$(ip -4 route show dev virbr0 2>/dev/null | head -n 1)
  if [ -z "$route_line" ]; then
    return 1
  fi
  BRIDGE_SUBNET=$(printf '%s\n' "$route_line" | awk '{print $1}')
  BRIDGE_IP=$(printf '%s\n' "$route_line" | awk '{for (i=1; i<=NF; i++) if ($i=="src") {print $(i+1); exit}}')
  [ -n "$BRIDGE_SUBNET" ] && [ -n "$BRIDGE_IP" ]
}

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

delete_snat_rules() {
  local vm_ip="$1"
  local public_ip="$2"
  [ -z "$vm_ip" ] && return 0
  [ -z "$public_ip" ] && return 0
  local lines
  lines=$(iptables -t nat -L POSTROUTING --line-numbers | awk -v ip="$vm_ip" -v pub="$public_ip" '
    $1 ~ /^[0-9]+$/ && $0 ~ "SNAT" && $0 ~ ip && $0 ~ pub {print $1}' | sort -rn)
  for num in $lines; do
    iptables -t nat -D POSTROUTING "$num" || true
  done
}

delete_hairpin_rules() {
  local vm_ip="$1"
  local vm_port="$2"
  local bridge_ip="$3"
  [ -z "$vm_ip" ] && return 0
  [ -z "$vm_port" ] && return 0
  [ -z "$bridge_ip" ] && return 0
  local lines
  lines=$(iptables -t nat -L POSTROUTING --line-numbers | awk -v ip="$vm_ip" -v port="dpt:${vm_port}" -v bridge="$bridge_ip" '
    $1 ~ /^[0-9]+$/ && $0 ~ "SNAT" && $0 ~ ip && $0 ~ port && $0 ~ bridge {print $1}' | sort -rn)
  for num in $lines; do
    iptables -t nat -D POSTROUTING "$num" || true
  done
}

log "Uninstalling VM ${VM_NAME}"
VM_IP=""
if VM_IP=$(get_vm_ip "$VM_NAME"); then
  log "Detected VM IP ${VM_IP} for ${VM_NAME}"
fi
BRIDGE_SUBNET=""
BRIDGE_IP=""
if get_bridge_info; then
  :
fi
cleanup_vm "$VM_NAME"
delete_nat_rules "$HOST_PORT" "$PUBLIC_IP"
if [ -z "$PUBLIC_PORT" ]; then
  PUBLIC_PORT="443"
fi
delete_forward_rules "$PUBLIC_PORT"
delete_snat_rules "$VM_IP" "$PUBLIC_IP"
delete_hairpin_rules "$VM_IP" "$PUBLIC_PORT" "$BRIDGE_IP"
log "Uninstall complete for ${VM_NAME}"
