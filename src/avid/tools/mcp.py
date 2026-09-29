"""stdio MCP 客户端：外部工具生态的入口（阶段 30e）。

MCP（Model Context Protocol）用 newline-delimited JSON-RPC over stdio 通信：一个
server 是一个子进程，``initialize`` 握手后 ``tools/list`` 列出它提供的工具，调用走
``tools/call``。本模块把每个远端工具包装成与内置工具同形的 ``(schema, impl)``，由
``tools.build_toolset`` 并进一次运行的工具集——循环、执行环节、前端工具卡都不感知
"MCP"的存在。

信任模型（与沙箱叙事的一致性）：

* server 进程**不经 bwrap**——它本来就是宿主上的独立进程，配置文件
  （``<工作区>/.avid/mcp.json``）本身就是用户的信任声明，运行时假装"沙箱过了"只会
  撒谎。调用仍过同一道 PreToolUse 权限闸门（manual 每工具问一次、auto 判不准即拒、
  full 放行，见 ``policy/engine.review_facts``）。
* server 崩溃/卡死只影响它自己的工具：失败回「错误：」文本（与其它工具同一约定），
  run 不中断；起不来的 server 在装配时记 warning 后跳过。
* 子 agent 不带 MCP 工具（SUB_TOOLS 是内置子集）——嵌套层拿外部工具的授权语义
  还没想清楚，v0 不开。

生命周期：``McpManager`` 由运行入口（svc/_run、cli）创建并 ``start_all``，进程随 run
起停（``finally`` 里 ``close``）。工具声明全部 EXCLUSIVE：MCP 工具的并发语义只有
server 自己知道，外面按最保守处理。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import itertools
import queue
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import workspace
from .registry import ToolImpl

logger = logging.getLogger("avid.tools.mcp")

MCP_TOOL_PREFIX = "mcp__"
HANDSHAKE_TIMEOUT_SECONDS = 10.0
DEFAULT_CALL_TIMEOUT_SECONDS = 120.0
PROTOCOL_VERSION = "2024-11-05"

#: close() 之后给 server 的体面退场时间，超时强杀。
TERMINATE_GRACE_SECONDS = 5.0


class McpError(RuntimeError):
    """一次 MCP 交互失败（进程没了、握手坏了、响应不合约定、超时）。"""


def mcp_config_path(workspace_root: str | None) -> Path:
    """工作区级配置：``<工作区根>/.avid/mcp.json``。"""
    base = Path(workspace_root) if workspace_root else Path(workspace.WORKSPACE_ROOT)
    return base / ".avid" / "mcp.json"


@dataclass(frozen=True)
class ServerSpec:
    """配置文件里的一条 server 声明。"""

    name: str
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    timeout: float = DEFAULT_CALL_TIMEOUT_SECONDS


def _load_specs(workspace_root: str | None) -> tuple[list[ServerSpec], list[str]]:
    """读配置 → server 声明。文件缺失是常态（返回空）；坏配置是 warning 不是异常。"""
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
    """MCP 工具的仓内名字：``mcp__<server>__<tool>``。"""
    return f"{MCP_TOOL_PREFIX}{server}__{tool}"


class McpServer:
    """一条 stdio server 连接：握手一次，之后逐个调用。"""

    def __init__(self, spec: ServerSpec, *, cwd: str | None = None) -> None:
        self.spec = spec
        self.cwd = cwd
        self._proc: subprocess.Popen[str] | None = None
        self._incoming: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._stderr: queue.Queue[str] = queue.Queue()
        self._ids = itertools.count(1)
        self._tools: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    # ---- 生命周期 ----

    def start(self) -> None:
        """起进程 + 握手 + 列工具。任何一步失败都收敛成 McpError。"""
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
        # 协议要求：握手完成后客户端发这条通知，server 才算进入工作状态。
        self._notify("notifications/initialized", {})
        listing = self._request("tools/list", {}, timeout=HANDSHAKE_TIMEOUT_SECONDS)
        tools = listing.get("tools") if isinstance(listing, dict) else None
        if not isinstance(tools, list):
            raise McpError("tools/list 的响应缺少 tools 数组")
        self._tools = [item for item in tools if isinstance(item, dict) and item.get("name")]

    def _read_stdout(self) -> None:
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
        self._incoming.put(None)  # EOF：等待方据此报"进程已退出"

    def _drain_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        for line in self._proc.stderr:
            self._stderr.put(line.rstrip("\n"))
            logger.debug("mcp server %s: %s", self.spec.name, line.rstrip("\n"))

    # ---- 协议 ----

    def _send(self, payload: dict[str, Any]) -> None:
        if self._proc is None or self._proc.poll() is not None:
            raise McpError(f"server {self.spec.name} 已退出")
        assert self._proc.stdin is not None
        try:
            self._proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise McpError(f"向 server {self.spec.name} 写请求失败：{exc}") from exc

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: dict[str, Any], *, timeout: float) -> Any:
        """发一个请求并等它的响应。独占调用保证每连接同时只有一个在飞。"""
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
                continue  # 下一圈由 remaining 判定超时
            if message is None:
                raise McpError(f"server {self.spec.name} 在等待 {method} 响应时退出")
            if not isinstance(message, dict):
                continue
            if message.get("id") != request_id:
                continue  # server 主动发的通知/请求：v0 不处理，只丢回队列旁
            if "error" in message:
                error = message["error"]
                detail = error.get("message") if isinstance(error, dict) else str(error)
                raise McpError(f"server {self.spec.name} 拒绝 {method}：{detail}")
            return message.get("result")

    # ---- 对上（toolset）的形状 ----

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def tools(self) -> list[dict[str, Any]]:
        return list(self._tools)

    def call(self, tool: str, arguments: dict[str, Any]) -> str:
        """调一个远端工具，返回给人读的文本。失败抛 McpError（impl 层收敛成文案）。"""
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
    """远端工具声明 → 与内置工具同形的 function calling 信封。

    MCP 的 inputSchema 是 JSON Schema：保留它的 properties/required，补上我们
    契约要求的 ``additionalProperties: False``；required 里指向不存在参数的条目
    剔掉（validate 会按 properties 校验）。
    """
    raw = tool.get("inputSchema")
    raw = raw if isinstance(raw, dict) else {}
    properties = raw.get("properties") if isinstance(raw.get("properties"), dict) else {}
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
    """一次运行的 MCP 工具来源：装配、托底、收尾都在这里。"""

    def __init__(self, workspace_root: str | None) -> None:
        self.workspace_root = workspace_root
        self._servers: list[McpServer] = []
        self._started = False

    def start_all(self) -> list[str]:
        """起配置里声明的全部 server。失败的记 warning 并跳过——不起 server 不该
        拦住一次运行（run 里还有 9 个内置工具可干活的）。幂等：重复调用只返回上次结果。"""
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
        """(schemas, impls)：与内置注册表同形，直接并进运行的工具清单。"""
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
        def call(arguments: dict[str, Any], *, state: Any = None) -> str:
            # ``state`` 收下不用：与其它 stateful 工具同签名，权限裁决已在
            # PreToolUse 闸门完成（engine → ask/ledger），这里只负责协议交互。
            try:
                return server.call(tool, arguments if isinstance(arguments, dict) else {})
            except McpError as exc:
                return f"错误：MCP {tool} 调用失败：{exc}"

        return call

    def processes_alive(self) -> int:
        return sum(1 for server in self._servers if server.alive)

    def close(self) -> None:
        for server in self._servers:
            try:
                server.close()
            except OSError:  # 进程已经在退出，别让收尾本身失败
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
