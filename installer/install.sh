#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALL_DIR="/opt/easy-enclave"
AGENT_SRC="$REPO_ROOT/agent"
INSTALLER_SRC="$REPO_ROOT/installer"

MODE="vm"
NON_INTERACTIVE=0
VM_NAME="ee-attestor"
VM_PORT="8000"
HOST_PORT=""
PUBLIC_PORT=""
ADMIN_PORT="8080"
PROXY_PORT="9090"
VM_IMAGE_TAG=""
VM_IMAGE_SHA256=""
AGENT_IMAGE=""
PUBLIC_IP=""
TDX_GUEST_VERSION="24.04"
TDX_REPO_REF="main"
TDX_REPO_DIR=""
OUTPUT_IMAGE=""
SKIP_BUILD=0
BASE_IMAGE=""
CONTROL_PLANE=0

usage() {
  cat <<'USAGE'
Easy Enclave installer

Usage:
  sudo ./installer/install.sh [options]

Options:
  --mode vm|host           Install agent VM (default) or host service
  --non-interactive        Disable prompts
  --vm-name NAME           Agent VM name (default: ee-attestor)
  --vm-port PORT           Agent VM port inside the VM (default: 8000)
  --host-port PORT         Host port to forward to the agent (default: same as --vm-port)
  --public-port PORT       VM port exposed publicly (default: 443)
  --admin-port PORT        Admin HTTP port inside the VM (default: 8080)
  --proxy-port PORT        Control-plane proxy port inside the VM (default: 9090)
  --public-ip IP           Public IP to bind DNAT rules (optional; defaults to all)
  --control-plane          Enable control-plane endpoints in agent VM
  --agent-image PATH       Use an existing pristine agent image
  --vm-image-tag TAG       Tag for pristine image/attestation
  --vm-image-sha256 SHA    Base image sha256 for vm_image_id (optional)
  --tdx-guest-version VER  TDX guest version (default: 24.04)
  --tdx-repo-ref REF       canonical/tdx repo ref (default: main)
  --tdx-repo-dir DIR       canonical/tdx repo dir (optional)
  --output-image PATH      Output path for pristine agent image
  --skip-build             Do not build pristine image
  --base-image PATH        Base TD image path override
  -h, --help               Show this help
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --mode) MODE="$2"; shift 2;;
    --non-interactive) NON_INTERACTIVE=1; shift;;
    --vm-name) VM_NAME="$2"; shift 2;;
    --vm-port) VM_PORT="$2"; shift 2;;
    --host-port) HOST_PORT="$2"; shift 2;;
    --public-port) PUBLIC_PORT="$2"; shift 2;;
    --admin-port) ADMIN_PORT="$2"; shift 2;;
    --proxy-port) PROXY_PORT="$2"; shift 2;;
    --public-ip) PUBLIC_IP="$2"; shift 2;;
    --control-plane) CONTROL_PLANE=1; shift;;
    --agent-image) AGENT_IMAGE="$2"; shift 2;;
    --vm-image-tag) VM_IMAGE_TAG="$2"; shift 2;;
    --vm-image-sha256) VM_IMAGE_SHA256="$2"; shift 2;;
    --tdx-guest-version) TDX_GUEST_VERSION="$2"; shift 2;;
    --tdx-repo-ref) TDX_REPO_REF="$2"; shift 2;;
    --tdx-repo-dir) TDX_REPO_DIR="$2"; shift 2;;
    --output-image) OUTPUT_IMAGE="$2"; shift 2;;
    --skip-build) SKIP_BUILD=1; shift;;
    --base-image) BASE_IMAGE="$2"; shift 2;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown option: $1"; usage; exit 1;;
  esac

done

if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (sudo)"
  exit 1
fi

if [ "$MODE" != "vm" ] && [ "$MODE" != "host" ]; then
  echo "--mode must be vm or host"
  exit 1
fi

prompt_if_empty() {
  local var_name="$1"
  local prompt="$2"
  local default="$3"
  if [ "$NON_INTERACTIVE" -eq 1 ] || [ ! -t 0 ]; then
    printf -v "$var_name" '%s' "$default"
    return
  fi
  local value
  read -r -p "$prompt [$default]: " value
  if [ -z "$value" ]; then
    value="$default"
  fi
  printf -v "$var_name" '%s' "$value"
}

check_qgs() {
  if command -v systemctl >/dev/null 2>&1; then
    if ! systemctl is-active --quiet qgsd; then
      echo "Error: QGS is not running (qgsd inactive)."
      echo "See installer/README.md for host setup."
      exit 1
    fi
  else
    if ! pgrep -x qgsd >/dev/null 2>&1; then
      echo "Error: QGS is not running (qgsd not found)."
      echo "See installer/README.md for host setup."
      exit 1
    fi
  fi
}

# Check TDX requirements
if [ ! -f /sys/module/kvm_intel/parameters/tdx ]; then
  echo "Warning: TDX kernel module not found"
else
  TDX_ENABLED=$(cat /sys/module/kvm_intel/parameters/tdx)
  if [ "$TDX_ENABLED" != "Y" ] && [ "$TDX_ENABLED" != "1" ]; then
    echo "Warning: TDX not enabled in kernel"
  fi
fi

check_qgs

# Check libvirt
if ! command -v virsh >/dev/null 2>&1; then
  echo "Error: virsh not found. Please install libvirt."
  exit 1
fi

if [ "$MODE" = "host" ]; then
  echo "Installing Easy Enclave Agent (host service)..."
  mkdir -p "$INSTALL_DIR"
  mkdir -p /var/lib/easy-enclave/deployments

  cp "$AGENT_SRC/agent.py" "$INSTALL_DIR/"
  cp "$AGENT_SRC/verify.py" "$INSTALL_DIR/"
  cp "$AGENT_SRC/ratls.py" "$INSTALL_DIR/"
  cp -r "$REPO_ROOT/control_plane" "$INSTALL_DIR/control_plane"
  cp -r "$REPO_ROOT/sdk/easyenclave" "$INSTALL_DIR/easyenclave"
  cp -r "$INSTALLER_SRC/templates" "$INSTALL_DIR/"

  apt-get update
  apt-get install -y python3-venv nginx openssl
  mkdir -p /etc/easy-enclave
  if [ -z "$PUBLIC_PORT" ]; then
    PUBLIC_PORT=443
  fi
  cat > /etc/easy-enclave/agent.env <<EOF
EE_MAIN_BIND=0.0.0.0
EE_MAIN_PORT=$VM_PORT
EE_ADMIN_BIND=127.0.0.1
EE_ADMIN_PORT=$ADMIN_PORT
EE_PROXY_BIND=127.0.0.1
EE_PROXY_PORT=$PROXY_PORT
EE_CONTROL_PLANE_ENABLED=$( [ "$CONTROL_PLANE" -eq 1 ] && echo true || echo false )
SEAL_VM=true
EOF
  for key in $(env | awk -F= '/^(EE_|CLOUDFLARE_)/ {print $1}'); do
    case "$key" in
      EE_MAIN_BIND|EE_MAIN_PORT|EE_ADMIN_BIND|EE_ADMIN_PORT|EE_PROXY_BIND|EE_PROXY_PORT|EE_CONTROL_PLANE_ENABLED|SEAL_VM)
        continue
        ;;
    esac
    echo "$key=${!key}" >> /etc/easy-enclave/agent.env
  done
  python3 - <<PY
from pathlib import Path
conf = Path("$INSTALLER_SRC/templates/nginx.conf").read_text().format(
    main_port="$VM_PORT",
    admin_port="$ADMIN_PORT",
    admin_tls_port="9443",
    admin_cert_path="/etc/nginx/ssl/admin.crt",
    admin_key_path="/etc/nginx/ssl/admin.key",
)
Path("/etc/nginx/nginx.conf").write_text(conf)
PY
  mkdir -p /etc/nginx/ssl
  if [ ! -f /etc/nginx/ssl/admin.key ]; then
    openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
      -subj "/CN=admin.easyenclave.local" \
      -keyout /etc/nginx/ssl/admin.key \
      -out /etc/nginx/ssl/admin.crt
  fi
  python3 -m venv "$INSTALL_DIR/venv"
  "$INSTALL_DIR/venv/bin/pip" install --no-cache-dir aiohttp cryptography requests

  cp "$INSTALLER_SRC/systemd/ee-agent.service" /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable --now nginx
  systemctl enable ee-agent

  echo "Installation complete."
  echo "Start with: sudo systemctl start ee-agent"
  exit 0
fi

# VM mode
DEFAULT_TAG="dev-$(date -u +%Y%m%d-%H%M%S)"
if [ -z "$VM_IMAGE_TAG" ]; then
  prompt_if_empty VM_IMAGE_TAG "VM image tag" "$DEFAULT_TAG"
fi

if [ -z "$OUTPUT_IMAGE" ]; then
  OUTPUT_IMAGE="/var/lib/easy-enclave/agent-pristine-${VM_IMAGE_TAG}.qcow2"
fi

if [ -z "$AGENT_IMAGE" ]; then
  if [ -f "$OUTPUT_IMAGE" ]; then
    AGENT_IMAGE="$OUTPUT_IMAGE"
  else
    if [ "$SKIP_BUILD" -eq 1 ]; then
      echo "Missing agent image and --skip-build set. Provide --agent-image or --output-image."
      exit 1
    fi
    BUILD_ARGS=(
      --build-pristine-agent-image
      --vm-image-tag "$VM_IMAGE_TAG"
      --tdx-guest-version "$TDX_GUEST_VERSION"
      --tdx-repo-ref "$TDX_REPO_REF"
      --output-image "$OUTPUT_IMAGE"
    )
    if [ -n "$VM_IMAGE_SHA256" ]; then
      BUILD_ARGS+=(--vm-image-sha256 "$VM_IMAGE_SHA256")
    fi
    if [ -n "$TDX_REPO_DIR" ]; then
      BUILD_ARGS+=(--tdx-repo-dir "$TDX_REPO_DIR")
    fi
    if [ -n "$BASE_IMAGE" ]; then
      BUILD_ARGS+=(--base-image "$BASE_IMAGE")
    fi
    echo "Building pristine agent image..."
    python3 "$INSTALLER_SRC/host.py" "${BUILD_ARGS[@]}"
    AGENT_IMAGE="$OUTPUT_IMAGE"
  fi
fi

chmod 666 "$AGENT_IMAGE" || true

echo "Starting agent VM..."
HOST_PORT_ARGS=()
if [ -n "$HOST_PORT" ]; then
  HOST_PORT_ARGS+=(--host-port "$HOST_PORT")
fi
PUBLIC_PORT_ARGS=()
if [ -z "$PUBLIC_PORT" ]; then
  PUBLIC_PORT=443
fi
PUBLIC_PORT_ARGS+=(--public-port "$PUBLIC_PORT")
ADMIN_PORT_ARGS=(--admin-port "$ADMIN_PORT")
PROXY_PORT_ARGS=(--proxy-port "$PROXY_PORT")
CONTROL_PLANE_ARGS=()
if [ "$CONTROL_PLANE" -eq 1 ]; then
  CONTROL_PLANE_ARGS+=(--control-plane)
fi
PUBLIC_IP_ARGS=()
if [ -n "$PUBLIC_IP" ]; then
  PUBLIC_IP_ARGS+=(--public-ip "$PUBLIC_IP")
fi
python3 "$INSTALLER_SRC/host.py" --agent --agent-image "$AGENT_IMAGE" --name "$VM_NAME" --port "$VM_PORT" "${HOST_PORT_ARGS[@]}" "${PUBLIC_PORT_ARGS[@]}" "${ADMIN_PORT_ARGS[@]}" "${PROXY_PORT_ARGS[@]}" "${CONTROL_PLANE_ARGS[@]}" "${PUBLIC_IP_ARGS[@]}"
