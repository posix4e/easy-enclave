#!/bin/bash
# Easy Enclave Agent Installation Script
set -e

INSTALL_DIR="/opt/easy-enclave"
AGENT_SRC="$(dirname "$0")/../action/src"

echo "Installing Easy Enclave Agent..."

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root (sudo)"
    exit 1
fi

# Check TDX requirements
echo "Checking TDX requirements..."
if [ ! -f /sys/module/kvm_intel/parameters/tdx ]; then
    echo "Warning: TDX kernel module not found"
else
    TDX_ENABLED=$(cat /sys/module/kvm_intel/parameters/tdx)
    if [ "$TDX_ENABLED" != "Y" ] && [ "$TDX_ENABLED" != "1" ]; then
        echo "Warning: TDX not enabled in kernel"
    else
        echo "TDX: enabled"
    fi
fi

# Check libvirt
if ! command -v virsh &> /dev/null; then
    echo "Error: virsh not found. Please install libvirt."
    exit 1
fi
echo "libvirt: available"

# Create directories
echo "Creating directories..."
mkdir -p "$INSTALL_DIR"
mkdir -p /var/lib/easy-enclave/deployments

# Copy agent files
echo "Copying agent files..."
cp "$AGENT_SRC/vm.py" "$INSTALL_DIR/"
cp "$AGENT_SRC/agent.py" "$INSTALL_DIR/"
cp -r "$(dirname "$0")/../action/templates" "$INSTALL_DIR/"

# Install systemd service
echo "Installing systemd service..."
cp "$(dirname "$0")/systemd/ee-agent.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable ee-agent

echo ""
echo "Installation complete!"
echo ""
echo "To start the agent:"
echo "  sudo systemctl start ee-agent"
echo ""
echo "To check status:"
echo "  sudo systemctl status ee-agent"
echo ""
echo "To view logs:"
echo "  sudo journalctl -u ee-agent -f"
echo ""
echo "Agent will listen on port 8000"
