"""stdio MCP client: the entry point for the external tool ecosystem.

It speaks newline-delimited JSON-RPC over stdio, and each remote tool is wrapped into the same
shape as a built-in tool.

"""


from __future__ import annotations

import itertools
import json
import logging
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import workspace
from .registry import ToolImpl

logger = logging.getLogger("avid.agent.tools.mcp")

MCP_TOOL_PREFIX = "mcp__"
HANDSHAKE_TIMEOUT_SECONDS = 10.0
DEFAULT_CALL_TIMEOUT_SECONDS = 120.0
PROTOCOL_VERSION = "2024-11-05"

#: Grace period given to a server on close before it is killed.
TERMINATE_GRACE_SECONDS = 5.0


class McpError(RuntimeError):
    """A failed MCP interaction: a dead process, a broken handshake, a bad reply or a timeout."""


def mcp_config_path(workspace_root: str | None) -> Path:
    """Returns the workspace-level configuration path, ``<root>/.avid/mcp.json``."""
    base = Path(workspace_root) if workspace_root else Path(workspace.WORKSPACE_ROOT)
    return base / ".avid" / "mcp.json"


@dataclass(frozen=True)
class ServerSpec:
    """One server declaration from the configuration file."""

    name: str
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    timeout: float = DEFAULT_CALL_TIMEOUT_SECONDS


def _load_specs(workspace_root: str | None) -> tuple[list[ServerSpec], list[str]]:
    """Reads the configuration into server declarations, warning rather than raising on errors."""
    path = mcp_config_path(workspace_root)
    if not path.exists():
        return [], []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [], [f"mcp.json 无法解析，已跳过全部 MCP server：{exc}"]
    entries = payload.get("servers") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return [], [f"mcp.json 缺少 servers 数组，已跳过：{path}"]

    specs: list[ServerSpec] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            warnings.append(f"mcp.json 第 {index} 项不是对象，已跳过")
            continue
        name = str(entry.get("name", "")).strip()
        command = str(entry.get("command", "")).strip()
        if not name or not command:
            warnings.append(f"mcp.json 第 {index} 项缺少 name 或 command，已跳过")
            continue
        if name in seen:
            warnings.append(f"mcp.json 里 server {name!r} 重复，只保留第一个")
            continue
        seen.add(name)
        args = entry.get("args")
        args = tuple(str(item) for item in args) if isinstance(args, list) else ()
        env = entry.get("env")
        env = (
            {str(k): str(v) for k, v in env.items()}
            if isinstance(env, dict)
            else {}
        )
        raw_timeout = entry.get("timeout_seconds")
        try:
            timeout = float(raw_timeout) if raw_timeout else DEFAULT_CALL_TIMEOUT_SECONDS
        except (TypeError, ValueError):
            timeout = DEFAULT_CALL_TIMEOUT_SECONDS
        specs.append(ServerSpec(name=name, command=command, args=args, env=env, timeout=timeout))
    return specs, warnings


def tool_name_of(server: str, tool: str) -> str:
    """Returns an MCP tool's in-repo name, ``mcp__<server>__<tool>``."""
    return f"{MCP_TOOL_PREFIX}{server}__{tool}"


class McpServer:
    """One stdio server connection: handshaken once, then called one request at a time."""

    def __init__(self, spec: ServerSpec, *, cwd: str | None = None) -> None:
        self.spec = spec
        self.cwd = cwd
        self._proc: subprocess.Popen[str] | None = None
        self._incoming: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._stderr: queue.Queue[str] = queue.Queue()
        self._ids = itertools.count(1)
        self._tools: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def start(self) -> None:
        """Starts the process, handshakes and lists tools, converging every failure on McpError."""
        env = {**os.environ, **self.spec.env}
        try:
            self._proc = subprocess.Popen(
                [self.spec.command, *self.spec.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.cwd,
                env=env,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise McpError(f"无法启动 {self.spec.command}：{exc}") from exc
        assert self._proc.stdout is not None and self._proc.stderr is not None
        threading.Thread(
            target=self._read_stdout, name=f"mcp-{self.spec.name}-out", daemon=True
        ).start()
        threading.Thread(
            target=self._drain_stderr, name=f"mcp-{self.spec.name}-err", daemon=True
        ).start()

        self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "avid", "version": "0"},
            },
            timeout=HANDSHAKE_TIMEOUT_SECONDS,
        )
        # The protocol requires this notification before the server starts serving.
        self._notify("notifications/initialized", {})
        listing = self._request("tools/list", {}, timeout=HANDSHAKE_TIMEOUT_SECONDS)
        tools = listing.get("tools") if isinstance(listing, dict) else None
        if not isinstance(tools, list):
            raise McpError("tools/list 的响应缺少 tools 数组")
        self._tools = [item for item in tools if isinstance(item, dict) and item.get("name")]

    def _read_stdout(self) -> None:
        """Reads reply lines into the queue, ignoring anything that is not JSON."""
        assert self._proc is not None and self._proc.stdout is not None
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                logger.debug("mcp server %s 发来非 JSON 行，忽略", self.spec.name)
                continue
            self._incoming.put(message)
        self._incoming.put(None)  # EOF, on which a waiter reports that the process exited

    def _drain_stderr(self) -> None:
        """Keeps the stderr pipe drained so the server cannot block on a full pipe."""
        assert self._proc is not None and self._proc.stderr is not None
        for line in self._proc.stderr:
            self._stderr.put(line.rstrip("\n"))
            logger.debug("mcp server %s: %s", self.spec.name, line.rstrip("\n"))

    def _send(self, payload: dict[str, Any]) -> None:
        """Writes one message as a single line, raising McpError if the process is gone."""
        if self._proc is None or self._proc.poll() is not None:
            raise McpError(f"server {self.spec.name} 已退出")
        assert self._proc.stdin is not None
        try:
            self._proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise McpError(f"向 server {self.spec.name} 写请求失败：{exc}") from exc

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        """Sends a notification, which carries no id and expects no reply."""
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: dict[str, Any], *, timeout: float) -> Any:
        """Sends a request and waits for its reply, with only one in flight per connection."""
        request_id = next(self._ids)
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise McpError(f"server {self.spec.name} 对 {method} 超时（{timeout:.1f}s）")
            try:
                message = self._incoming.get(timeout=remaining)
            except queue.Empty:
                continue  # the next turn re-tests the deadline
            if message is None:
                raise McpError(f"server {self.spec.name} 在等待 {method} 响应时退出")
            if message.get("id") != request_id:
                continue  # a server-initiated notification or request, ignored in v0
            if "error" in message:
                error = message["error"]
                detail = error.get("message") if isinstance(error, dict) else str(error)
                raise McpError(f"server {self.spec.name} 拒绝 {method}：{detail}")
            return message.get("result")

    @property
    def alive(self) -> bool:
        """Reports whether the server process is still running."""
        return self._proc is not None and self._proc.poll() is None

    def tools(self) -> list[dict[str, Any]]:
        """Returns the tool declarations listed at handshake time."""
        return list(self._tools)

    def call(self, tool: str, arguments: dict[str, Any]) -> str:
        """Calls one remote tool, returning readable text and raising McpError on failure."""
        result = self._request(
            "tools/call", {"name": tool, "arguments": arguments}, timeout=self.spec.timeout
        )
        if not isinstance(result, dict):
            raise McpError(f"tools/call 的响应不是对象：{repr(result)[:200]}")
        content = result.get("content")
        parts: list[str] = []
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
        text = "\n".join(part for part in parts if part)
        if result.get("isError"):
            raise McpError(text or "server 报告调用失败")
        return text

    def close(self) -> None:
        """Terminates the server within the grace period and kills it if that is not enough."""
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=TERMINATE_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=TERMINATE_GRACE_SECONDS)


def _schema_of(server_name: str, tool: dict[str, Any]) -> dict[str, Any]:
    """Converts one remote tool declaration into a function-calling envelope, dropping any
    required name that has no matching property."""
    raw = tool.get("inputSchema")
    raw = raw if isinstance(raw, dict) else {}
    schema_properties = raw.get("properties")
    properties = schema_properties if isinstance(schema_properties, dict) else {}
    required = [
        item
        for item in raw.get("required") or []
        if isinstance(item, str) and item in properties
    ]
    return {
        "type": "function",
        "function": {
            "name": tool_name_of(server_name, str(tool["name"])),
            "description": str(tool.get("description") or f"{server_name} 提供的 {tool['name']}"),
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


class McpManager:
    """The MCP tool source for one run: it assembles, backstops and closes the servers."""

    def __init__(self, workspace_root: str | None) -> None:
        self.workspace_root = workspace_root
        self._servers: list[McpServer] = []
        self._started = False

    def start_all(self) -> list[str]:
        """Starts every declared server, skipping failures with a warning; idempotent."""
        if self._started:
            return []
        self._started = True
        specs, warnings = _load_specs(self.workspace_root)
        for spec in specs:
            server = McpServer(spec, cwd=self.workspace_root)
            try:
                server.start()
            except McpError as exc:
                warnings.append(f"MCP server {spec.name} 启动失败：{exc}")
                continue
            self._servers.append(server)
        if self._servers:
            logger.info(
                "MCP：启动 %d 个 server，共 %d 个工具",
                len(self._servers),
                sum(len(server.tools()) for server in self._servers),
            )
        return warnings

    def toolset(self) -> tuple[list[dict[str, Any]], dict[str, ToolImpl]]:
        """Returns schemas and handlers in the built-in shape, ready to merge into the tool set."""
        schemas: list[dict[str, Any]] = []
        impls: dict[str, ToolImpl] = {}
        for server in self._servers:
            for tool in server.tools():
                name = tool_name_of(server.spec.name, str(tool["name"]))
                schemas.append(_schema_of(server.spec.name, tool))
                impls[name] = self._impl(server, str(tool["name"]))
        return schemas, impls

    @staticmethod
    def _impl(server: McpServer, tool: str) -> ToolImpl:
        """Wraps one remote tool as a handler, turning an McpError into error text."""

        def call(arguments: dict[str, Any], *, state: Any = None) -> str:
            # ``state`` is accepted and unused, matching the other stateful signatures; the
            # permission verdict is already made by the gate, so this only speaks the protocol.
            try:
                return server.call(tool, arguments if isinstance(arguments, dict) else {})
            except McpError as exc:
                return f"错误：MCP {tool} 调用失败：{exc}"

        return call

    def processes_alive(self) -> int:
        """Counts the servers whose process is still running."""
        return sum(1 for server in self._servers if server.alive)

    def close(self) -> None:
        """Closes every server, swallowing an OSError so shutdown itself cannot fail."""
        for server in self._servers:
            try:
                server.close()
            except OSError:  # the process is already exiting, so do not fail the shutdown
                logger.debug("MCP server %s 收尾失败", server.spec.name, exc_info=True)
        self._servers = []


__all__ = [
    "DEFAULT_CALL_TIMEOUT_SECONDS",
    "MCP_TOOL_PREFIX",
    "McpError",
    "McpManager",
    "McpServer",
    "ServerSpec",
    "mcp_config_path",
    "tool_name_of",
]
