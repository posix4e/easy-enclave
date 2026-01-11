#!/usr/bin/env bash
set -euo pipefail

# Reset and reinstall control + contacts agents with explicit IP/port mapping.

CONTROL_VM_NAME="ee-control"
CONTROL_VM_PORT="8000"
CONTROL_HOST_PORT="443"
CONTROL_PUBLIC_IP="57.130.10.246"

CONTACT_VM_NAME="ee-contacts"
CONTACT_VM_PORT="8001"
CONTACT_HOST_PORT="443"
CONTACT_PUBLIC_IP="57.130.34.193"

VM_IMAGE_TAG=""
CLEANUP_VMS="ee-control ee-contacts ee-workload"

usage() {
  cat <<'USAGE'
reset_agents.sh

Usage: sudo ./installer/reset_agents.sh [options]

Options:
  --control-vm-name NAME       Control agent VM name (default: ee-control)
  --control-vm-port PORT       Control agent VM port inside VM (default: 8000)
  --control-host-port PORT     Host port for control agent (default: 443)
  --control-public-ip IP       Public IP mapped to control agent (default: 57.130.10.246)
  --contact-vm-name NAME       Contacts agent VM name (default: ee-contacts)
  --contact-vm-port PORT       Contacts agent VM port inside VM (default: 8001)
  --contact-host-port PORT     Host port for contacts agent (default: 443)
  --contact-public-ip IP       Public IP mapped to contacts agent (default: 57.130.34.193)
  --vm-image-tag TAG           Tag for pristine image build (passed to install-agent.sh)
  --cleanup-vms "A B ..."      Extra VM names to destroy/undefine (default: ee-control ee-contacts ee-workload)
  -h, --help                   Show this help
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --control-vm-name) CONTROL_VM_NAME="$2"; shift 2;;
    --control-vm-port) CONTROL_VM_PORT="$2"; shift 2;;
    --control-host-port) CONTROL_HOST_PORT="$2"; shift 2;;
    --control-public-ip) CONTROL_PUBLIC_IP="$2"; shift 2;;
    --contact-vm-name) CONTACT_VM_NAME="$2"; shift 2;;
    --contact-vm-port) CONTACT_VM_PORT="$2"; shift 2;;
    --contact-host-port) CONTACT_HOST_PORT="$2"; shift 2;;
    --contact-public-ip) CONTACT_PUBLIC_IP="$2"; shift 2;;
    --vm-image-tag) VM_IMAGE_TAG="$2"; shift 2;;
    --cleanup-vms) CLEANUP_VMS="$2"; shift 2;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown option: $1" >&2; usage; exit 1;;
  esac
done

if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (sudo)" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

log() { printf '%s\n' "$*" >&2; }

delete_nat_rules() {
  local host_port="$1"
  local ip="$2"
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
  for chain in FORWARD LIBVIRT_FWI; do
    local lines
    lines=$(iptables -L "$chain" --line-numbers | awk -v port="dpt:${vm_port}" '$1 ~ /^[0-9]+$/ && $0 ~ port {print $1}' | sort -rn)
    for num in $lines; do
      iptables -D "$chain" "$num" || true
    done
  done
}

cleanup_vm() {
  local name="$1"
  virsh destroy "$name" >/dev/null 2>&1 || true
  virsh undefine "$name" --nvram --managed-save --remove-all-storage >/dev/null 2>&1 || \
    virsh undefine "$name" --nvram --remove-all-storage >/dev/null 2>&1 || true
}

get_vm_ip() {
  local name="$1"
  local ip=""
  ip=$(virsh domifaddr "$name" --source lease 2>/dev/null | awk '/ipv4/ {print $4}' | head -n1 | cut -d/ -f1)
  if [ -z "$ip" ]; then
    ip=$(virsh net-dhcp-leases default 2>/dev/null | awk -v n="$name" '$0 ~ n && /ipv4/ {print $5}' | head -n1 | cut -d/ -f1)
  fi
  printf '%s' "$ip"
}

setup_forward() {
  local pub_ip="$1"
  local host_port="$2"
  local vm_ip="$3"
  local vm_port="$4"

  delete_nat_rules "$host_port" "$pub_ip"
  delete_forward_rules "$vm_port"

  iptables -t nat -A PREROUTING -d "$pub_ip" -p tcp --dport "$host_port" -j DNAT --to-destination "${vm_ip}:${vm_port}"
  iptables -t nat -A OUTPUT -d "$pub_ip" -p tcp --dport "$host_port" -j DNAT --to-destination "${vm_ip}:${vm_port}"
  iptables -I LIBVIRT_FWI -p tcp -d "$vm_ip" --dport "$vm_port" -j ACCEPT || true
  iptables -A FORWARD -p tcp -d "$vm_ip" --dport "$vm_port" -j ACCEPT
  log "Forwarded ${pub_ip}:${host_port} -> ${vm_ip}:${vm_port}"
}

install_agent() {
  local name="$1"
  local vm_port="$2"
  local host_port="$3"
  local tag="$4"

  local args=(--non-interactive --vm-name "$name" --vm-port "$vm_port" --host-port "$host_port")
  if [ -n "$tag" ]; then
    args+=(--vm-image-tag "$tag")
  fi
  log "Installing agent ${name} (vm_port=${vm_port} host_port=${host_port} tag=${tag:-auto})"
  "$REPO_ROOT/install-agent.sh" "${args[@]}"
}

verify_agent() {
  local port="$1"
  log "Verifying https://127.0.0.1:${port}/health"
  if ! curl -fsSk --max-time 10 "https://127.0.0.1:${port}/health"; then
    log "Warning: health check failed on port ${port}"
  fi
}

log "=== Cleanup phase ==="
for vm in $CLEANUP_VMS; do
  cleanup_vm "$vm"
done
delete_nat_rules "$CONTROL_HOST_PORT" "$CONTROL_PUBLIC_IP"
delete_nat_rules "$CONTACT_HOST_PORT" "$CONTACT_PUBLIC_IP"
delete_forward_rules "$CONTROL_VM_PORT"
delete_forward_rules "$CONTACT_VM_PORT"

log "=== Install control agent ==="
install_agent "$CONTROL_VM_NAME" "$CONTROL_VM_PORT" "$CONTROL_HOST_PORT" "$VM_IMAGE_TAG"
CONTROL_VM_IP=$(get_vm_ip "$CONTROL_VM_NAME" || true)
log "Control VM IP: ${CONTROL_VM_IP:-unknown}"
if [ -n "$CONTROL_VM_IP" ]; then
  setup_forward "$CONTROL_PUBLIC_IP" "$CONTROL_HOST_PORT" "$CONTROL_VM_IP" "$CONTROL_VM_PORT"
fi

log "=== Install contacts agent ==="
install_agent "$CONTACT_VM_NAME" "$CONTACT_VM_PORT" "$CONTACT_HOST_PORT" "$VM_IMAGE_TAG"
CONTACT_VM_IP=$(get_vm_ip "$CONTACT_VM_NAME" || true)
log "Contacts VM IP: ${CONTACT_VM_IP:-unknown}"
if [ -n "$CONTACT_VM_IP" ]; then
  setup_forward "$CONTACT_PUBLIC_IP" "$CONTACT_HOST_PORT" "$CONTACT_VM_IP" "$CONTACT_VM_PORT"
fi

log "=== Verification ==="
verify_agent "$CONTROL_HOST_PORT"
verify_agent "$CONTACT_HOST_PORT"

log "=== Summary ==="
log "Control: ${CONTROL_PUBLIC_IP}:${CONTROL_HOST_PORT} -> ${CONTROL_VM_NAME} (${CONTROL_VM_IP:-unknown}):${CONTROL_VM_PORT}"
log "Contacts: ${CONTACT_PUBLIC_IP}:${CONTACT_HOST_PORT} -> ${CONTACT_VM_NAME} (${CONTACT_VM_IP:-unknown}):${CONTACT_VM_PORT}"
