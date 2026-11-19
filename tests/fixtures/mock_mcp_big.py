"""A minimal MCP stdio server whose tools/list is larger than asyncio's 64 KiB default."""
import json
import sys

for line in sys.stdin:
    try:
        msg = json.loads(line)
    except Exception:
        continue
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        out = {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "big", "version": "1"},
        }}
    elif method == "tools/list":
        out = {"jsonrpc": "2.0", "id": mid, "result": {"tools": [{
            "name": "huge", "description": "x" * 100000,
            "inputSchema": {"type": "object", "properties": {}},
        }]}}
    elif mid is not None:
        out = {"jsonrpc": "2.0", "id": mid, "result": {}}
    else:
        continue
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()
