"""一个最小但**真实**的 stdio MCP server：newline-delimited JSON-RPC。

按协议回 initialize / tools/list / tools/call 三个方法；工具只有一个 ``echo``。
命令行开关：``--crash`` 启动即退出（模拟 server 起不来）、``--delay N`` 每次调用
先睡 N 秒（模拟卡死，测客户端超时）。
"""

from __future__ import annotations

import json
import sys
import time

PROTOCOL_VERSION = "2024-11-05"


def respond(request: dict) -> dict | None:
    method = request.get("method")
    request_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-echo", "version": "0"},
            },
        }
    if method == "notifications/initialized":
        return None  # 通知不回
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": [
                    {
                        "name": "echo",
                        "description": "把参数原样回显，测试用。",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string", "description": "要回显的文本"}
                            },
                            "required": ["text"],
                        },
                    }
                ]
            },
        }
    if method == "tools/call":
        arguments = (request.get("params") or {}).get("arguments") or {}
        if arguments.get("fail"):
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": f"boom: {arguments}"}],
                    "isError": True,
                },
            }
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": f"echo:{arguments.get('text', '')}"}],
                "isError": False,
            },
        }
    if request_id is not None:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"unknown method {method}"},
        }
    return None


def main() -> int:
    if "--crash" in sys.argv[1:]:
        print("fake server crashing on purpose", file=sys.stderr)
        return 3
    delay = 0.0
    if "--delay" in sys.argv[1:]:
        delay = float(sys.argv[sys.argv.index("--delay") + 1])

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        if request.get("method") == "tools/call" and delay:
            time.sleep(delay)
        response = respond(request)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
