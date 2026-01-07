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
VM_IMAGE_TAG=""
VM_IMAGE_SHA256=""
AGENT_IMAGE=""
TDX_GUEST_VERSION="24.04"
TDX_REPO_REF="main"
TDX_REPO_DIR=""
OUTPUT_IMAGE=""
SKIP_BUILD=0
BASE_IMAGE=""

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
  cp -r "$INSTALLER_SRC/templates" "$INSTALL_DIR/"

  cp "$INSTALLER_SRC/systemd/ee-agent.service" /etc/systemd/system/
  systemctl daemon-reload
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
python3 "$INSTALLER_SRC/host.py" --agent --agent-image "$AGENT_IMAGE" --name "$VM_NAME" --port "$VM_PORT" "${HOST_PORT_ARGS[@]}"
