#!/bin/bash
exec > /opt/workload/start.log 2>&1
echo "=== start.sh starting at $(date) ==="
set -x
cd /opt/workload
if [ -f /opt/workload/.env ]; then
    set -a
    . /opt/workload/.env
    set +a
fi

if [ "${ENABLE_SSH:-}" = "true" ]; then
    systemctl enable --now ssh 2>/dev/null || true
    if [ -f /opt/workload/authorized_keys ]; then
        install -d -m 700 /home/ubuntu/.ssh
        install -m 600 /opt/workload/authorized_keys /home/ubuntu/.ssh/authorized_keys
        chown -R ubuntu:ubuntu /home/ubuntu/.ssh
    fi
    if [ -n "${UNSEAL_PASSWORD:-}" ]; then
        sed -i 's/^#\\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
        echo "ubuntu:${UNSEAL_PASSWORD}" | chpasswd
        systemctl restart ssh 2>/dev/null || true
    fi
fi

# Generate TDX quote first
echo "Starting quote generation..."
python3 /opt/workload/get-quote.py > /opt/workload/quote.json 2>/opt/workload/quote.log || echo '{"success": false, "error": "quote generation failed"}' > /opt/workload/quote.json
chmod 644 /opt/workload/quote.json /opt/workload/quote.log 2>/dev/null || true
echo "Quote generation done: $(cat /opt/workload/quote.json | head -c 100)..."

# Create response HTML
echo "Creating index.html..."
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

# Start simple HTTP server on port {port}
echo "Starting HTTP server on port {port}..."
cd /opt/workload
nohup python3 -m http.server {port} > /opt/workload/http.log 2>&1 &
HTTP_PID=$!
echo "HTTP server started with PID $HTTP_PID"
sleep 2

# Verify it's running
if kill -0 $HTTP_PID 2>/dev/null; then
    echo "HTTP server is running"
    netstat -tlnp 2>/dev/null | grep {port} || ss -tlnp | grep {port} || echo "Port check failed but process running"
else
    echo "ERROR: HTTP server died immediately"
    cat /opt/workload/http.log
fi

# Signal ready
touch /opt/workload/ready
echo "=== start.sh completed at $(date) ==="

# Seal VM access if requested
if [ "${SEAL_VM:-}" = "true" ]; then
    echo "Sealing VM access..."
    systemctl disable --now ssh 2>/dev/null || true
    systemctl mask getty@ttyS0.service serial-getty@ttyS0.service 2>/dev/null || true
fi
