#!/usr/bin/env python3
import base64
import json
import os
import tempfile

def get_tdx_quote():
    tsm_path = "/sys/kernel/config/tsm/report"
    if not os.path.exists(tsm_path):
        raise RuntimeError(f"configfs-tsm not available at {tsm_path}")

    report_dir = tempfile.mkdtemp(dir=tsm_path)
    inblob = os.path.join(report_dir, "inblob")
    outblob = os.path.join(report_dir, "outblob")

    with open(inblob, 'wb') as f:
        f.write(b'\x00' * 64)

    with open(outblob, 'rb') as f:
        data = f.read()

    if len(data) == 0:
        raise RuntimeError("Empty quote from configfs-tsm")

    return data

try:
    quote = get_tdx_quote()
    if len(quote) < 100:
        raise RuntimeError(f"Quote too small ({len(quote)} bytes)")
    print(json.dumps({
        "success": True,
        "quote": base64.b64encode(quote).decode(),
        "size": len(quote)
    }))
except Exception as e:
    print(json.dumps({"success": False, "error": str(e)}))
