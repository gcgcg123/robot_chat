"""Device-side MCP: the xiaozhi firmware's tool channel.

Voice control of the device itself (volume, brightness, theme, camera...) is not
a protocol feature of its own -- the firmware exposes those as **MCP tools** and
expects the *server* to drive the JSON-RPC conversation over the same WebSocket::

    server -> {"type":"mcp","payload":{"jsonrpc":"2.0","id":1,"method":"initialize",...}}
    device -> {"type":"mcp","payload":{"jsonrpc":"2.0","id":1,"result":{"serverInfo":{...}}}}
    server -> {"type":"mcp","payload":{"jsonrpc":"2.0","id":2,"method":"tools/list"}}
    device -> {"type":"mcp","payload":{"jsonrpc":"2.0","id":2,"result":{"tools":[...]}}}
    server -> {"type":"mcp","payload":{"jsonrpc":"2.0","id":11,"method":"tools/call",
                                       "params":{"name":"self.audio_speaker.set_volume",
                                                 "arguments":{"volume":60}}}}
    device -> {"type":"mcp","payload":{"jsonrpc":"2.0","id":11,"result":{"content":[...]}}}

Without the handshake the device's tools are simply invisible to the LLM, which
is why "把音量调到 60" used to be answered in words and nothing else.  The older
``iot`` protocol (declared descriptors + ``{"name":device,"method":...}``
commands) is folded into the same toolset so both firmware generations work.

Pure transport: this module knows nothing about the dialogue pipeline.  It is
handed a ``send`` coroutine by the session and a dict of payloads comes back.
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

MCP_PROTOCOL_VERSION = "2024-11-05"
INITIALIZE_ID = 1
TOOLS_LIST_ID = 2
#: First JSON-RPC id handed out for ``tools/call`` (1 and 2 are the handshake).
CALL_ID_BASE = 10
#: A tool call longer than this is treated as a device that will never answer.
DEFAULT_TIMEOUT = 12.0
#: How long the handshake may take before the firmware is declared non-MCP.
HANDSHAKE_TIMEOUT = 6.0

_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")
_MAX_TOOLS = 48


def sanitize_tool_name(name: str) -> str:
    """``self.audio_speaker.set_volume`` -> ``self_audio_speaker_set_volume``.

    Model APIs only accept ``[A-Za-z0-9_-]`` in function names, and the device's
    names are dotted paths, so the mapping has to be reversible through
    :attr:`DeviceTool.name`.
    """
    cleaned = _UNSAFE.sub("_", str(name or "").strip())
    return cleaned[:64] or "tool"


@dataclass
class DeviceTool:
    """One callable the firmware declared, in either transport."""

    name: str
    description: str = ""
    input_schema: dict = field(default_factory=dict)
    transport: str = "mcp"
    #: For ``transport == "iot"``: the device whose method this is.
    device: str = ""

    @property
    def llm_name(self) -> str:
        return sanitize_tool_name(self.name)

    def as_llm_tool(self) -> dict[str, Any]:
        schema = self.input_schema if isinstance(self.input_schema, dict) else {}
        if schema.get("type") != "object" or not isinstance(schema.get("properties", {}), dict):
            schema = {}
        parameters = {
            "type": "object",
            "properties": schema.get("properties") or {},
            "required": [item for item in (schema.get("required") or []) if isinstance(item, str)],
        }
        if not parameters["properties"]:
            # A no-argument tool: models still need an object to fill in.
            parameters["properties"] = {}
        return {
            "type": "function",
            "function": {
                "name": self.llm_name,
                "description": (self.description or self.name)[:512],
                "parameters": parameters,
            },
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "llm_name": self.llm_name,
            "description": self.description,
            "transport": self.transport,
            "parameters": sorted((self.input_schema or {}).get("properties", {}) or {}),
            "device": self.device,
        }


class DeviceToolset:
    """The tools one device declared, addressable by either name form."""

    def __init__(self) -> None:
        self._by_name: dict[str, DeviceTool] = {}
        self._by_llm_name: dict[str, DeviceTool] = {}

    def add(self, tool: DeviceTool) -> bool:
        if not tool.name or len(self._by_name) >= _MAX_TOOLS:
            return False
        self._by_name[tool.name] = tool
        self._by_llm_name[tool.llm_name] = tool
        return True

    def get(self, key: str) -> DeviceTool | None:
        return self._by_name.get(key) or self._by_llm_name.get(key)

    def all(self) -> list[DeviceTool]:
        return list(self._by_name.values())

    def llm_tools(self) -> list[dict[str, Any]]:
        return [tool.as_llm_tool() for tool in self._by_name.values()]

    def snapshot(self) -> list[dict[str, Any]]:
        return [tool.as_dict() for tool in self._by_name.values()]

    def __len__(self) -> int:
        return len(self._by_name)


class DeviceMcpClient:
    """Drives the JSON-RPC handshake and tool calls for one session.

    ``send`` receives a **complete** message envelope (for example
    ``{"type":"mcp","payload":{...}}``), so the session decides how a frame is
    written and this module never touches the socket directly.
    """

    def __init__(self, send: Callable[[dict], Awaitable[None]], *, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._send = send
        self.timeout = timeout
        self.tools = DeviceToolset()
        self.ready = False
        self.available = False
        self.reason = ""
        self.server_info: dict[str, Any] = {}
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = CALL_ID_BASE
        self._closed = False

    # ------------------------------------------------------------------ sending

    async def _call(self, payload: dict) -> None:
        await self._send({"type": "mcp", "payload": payload})

    async def start(self) -> dict[str, Any]:
        """Handshake, then fetch the tool list.  Never raises.

        Returns a status dict the session records as an event: an empty toolset
        with ``available: False`` is the honest answer for a firmware built
        without MCP, and it is what tells an operator why voice control has no
        effect instead of leaving them guessing.
        """
        self.available = True
        try:
            await self._call(self._initialize_payload())
        except Exception as exc:  # noqa: BLE001 - a dead socket must not raise here
            return self._status(f"send_failed:{type(exc).__name__}")
        try:
            await self._wait_ready(HANDSHAKE_TIMEOUT)
        except asyncio.TimeoutError:
            return self._status("no_mcp_response")
        except Exception as exc:  # noqa: BLE001
            return self._status(f"handshake_error:{type(exc).__name__}")
        return self._status("")

    def _status(self, reason: str) -> dict[str, Any]:
        # A firmware without MCP answers nothing, so "no tools" is only reported
        # as unavailable when the handshake itself failed; an empty-but-answered
        # tool list is a *working* device that simply exposes nothing.
        if reason:
            self.reason = reason
            self.ready = False
        self.available = not reason
        return {
            "available": self.available,
            "ready": self.ready,
            "tools": len(self.tools),
            "reason": self.reason,
            "server_info": self.server_info,
            "names": [tool.name for tool in self.tools.all()],
        }

    @staticmethod
    def _initialize_payload() -> dict:
        return {
            "jsonrpc": "2.0",
            "id": INITIALIZE_ID,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"roots": {"listChanged": True}, "sampling": {}},
                "clientInfo": {"name": "XiaozhiClient", "version": "1.0.0"},
            },
        }

    async def _wait_ready(self, timeout: float) -> None:
        """Poll until the tool list landed, the device errored, or time ran out."""
        deadline = asyncio.get_running_loop().time() + timeout
        while not self.ready:
            if self._closed:
                raise RuntimeError("session_closed")
            if self.reason:
                raise RuntimeError(self.reason)
            if asyncio.get_running_loop().time() >= deadline:
                raise asyncio.TimeoutError
            await asyncio.sleep(0.05)

    # ------------------------------------------------------------------ inbound

    async def handle(self, payload: Any) -> None:
        """Route one ``{"type":"mcp"}`` payload coming from the device."""
        if not isinstance(payload, dict):
            return
        message_id = payload.get("id")
        try:
            message_id = int(message_id) if message_id is not None else 0
        except (TypeError, ValueError):
            message_id = 0

        if "error" in payload:
            error = payload.get("error")
            message = str(error.get("message") if isinstance(error, dict) else error)[:200]
            self.reason = f"mcp_error:{message}"
            if message_id not in (INITIALIZE_ID, TOOLS_LIST_ID):
                self._reject(message_id, RuntimeError(self.reason))
            return

        if "result" not in payload:
            # A request *from* the device (the firmware may call server tools).
            # Nothing is served over this channel yet, so answer an error instead
            # of stalling the firmware on a call that will never be resolved.
            if "method" in payload:
                await self._respond_unsupported(message_id, str(payload.get("method", "")))
            return

        result = payload["result"]
        if message_id == INITIALIZE_ID:
            server_info = (result or {}).get("serverInfo") if isinstance(result, dict) else None
            self.server_info = server_info if isinstance(server_info, dict) else {}
            await self._call({"jsonrpc": "2.0", "id": TOOLS_LIST_ID, "method": "tools/list"})
            return
        if message_id == TOOLS_LIST_ID:
            await self._absorb_tools(result)
            return
        self._resolve(message_id, result)

    async def _respond_unsupported(self, message_id: int, method: str) -> None:
        try:
            await self._call(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "error": {"code": -32601, "message": f"method_not_supported:{method}"},
                }
            )
        except Exception:  # noqa: BLE001 - socket already gone
            pass

    async def _absorb_tools(self, result: Any) -> None:
        tools = result.get("tools") if isinstance(result, dict) else None
        for item in tools or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            schema = item.get("inputSchema")
            self.tools.add(
                DeviceTool(
                    name=name,
                    description=str(item.get("description", "") or ""),
                    input_schema=schema if isinstance(schema, dict) else {},
                    transport="mcp",
                )
            )
        cursor = result.get("nextCursor") if isinstance(result, dict) else None
        if cursor:
            await self._call(
                {"jsonrpc": "2.0", "id": TOOLS_LIST_ID, "method": "tools/list", "params": {"cursor": str(cursor)}}
            )
            return
        self.ready = True

    # ------------------------------------------------------------- legacy iot

    def add_iot_descriptors(self, descriptors: Any) -> int:
        """Register ``iot`` protocol methods as tools.

        Accepts both shapes seen in the wild: the firmware's list of descriptor
        objects, and the ``{device: {...}}`` mapping the earlier dashboard code
        assumed.  Each method becomes ``<device>_<method>``.
        """
        added = 0
        for device, spec in _iter_descriptors(descriptors):
            if not isinstance(spec, dict):
                continue
            methods = spec.get("methods")
            if not isinstance(methods, dict):
                continue
            for method_name, method in methods.items():
                if not isinstance(method, dict):
                    method = {}
                parameters = method.get("parameters")
                properties: dict[str, Any] = {}
                required: list[str] = []
                if isinstance(parameters, dict):
                    for key, value in parameters.items():
                        info = value if isinstance(value, dict) else {}
                        properties[str(key)] = {
                            "type": _json_type(info.get("type")),
                            "description": str(info.get("description", "") or ""),
                        }
                        required.append(str(key))
                if self.tools.add(
                    DeviceTool(
                        name=f"{device}_{method_name}",
                        description=str(method.get("description", "") or f"{device} {method_name}"),
                        input_schema={"type": "object", "properties": properties, "required": required},
                        transport="iot",
                        device=str(device),
                    )
                ):
                    added += 1
        if added:
            self.ready = True
            self.available = True
        return added

    # ------------------------------------------------------------------ calling

    async def call_tool(self, name: str, arguments: Any = None, *, timeout: float | None = None) -> str:
        """Run one tool and return its textual result (or raise)."""
        tool = self.tools.get(str(name))
        if tool is None:
            raise ValueError(f"tool_not_declared:{name}")
        arguments = _as_arguments(arguments)
        if tool.transport == "iot":
            return await self._call_iot(tool, arguments)
        return await self._call_mcp(tool, arguments, timeout=timeout)

    async def _call_mcp(self, tool: DeviceTool, arguments: dict, *, timeout: float | None = None) -> str:
        if self._closed:
            raise RuntimeError("session_closed")
        message_id = self._next_id
        self._next_id += 1
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        await self._call(
            {
                "jsonrpc": "2.0",
                "id": message_id,
                "method": "tools/call",
                "params": {"name": tool.name, "arguments": arguments},
            }
        )
        try:
            result = await asyncio.wait_for(future, timeout=timeout or self.timeout)
        except asyncio.TimeoutError:
            self._pending.pop(message_id, None)
            raise TimeoutError(f"tool_timeout:{tool.name}") from None
        return _flatten_result(result)

    async def _call_iot(self, tool: DeviceTool, arguments: dict) -> str:
        method = tool.name[len(tool.device) + 1 :] if tool.device else tool.name
        command: dict[str, Any] = {"name": tool.device or tool.name, "method": method}
        if arguments:
            command["parameters"] = arguments
        await self._send({"type": "iot", "commands": [command]})
        # The iot protocol has an ack, but it is not paired to a request id, so a
        # blocking wait would deadlock on firmware that stays silent.  Reporting
        # "sent" is the truth we actually have.
        return f"已下發指令：{tool.name} {json.dumps(arguments, ensure_ascii=False)}".strip()

    # ------------------------------------------------------------------ futures

    def _resolve(self, message_id: int, result: Any) -> None:
        future = self._pending.pop(message_id, None)
        if future is not None and not future.done():
            future.set_result(result)

    def _reject(self, message_id: int, error: Exception) -> None:
        future = self._pending.pop(message_id, None)
        if future is not None and not future.done():
            future.set_exception(error)

    def close(self, reason: str = "session_closed") -> None:
        self._closed = True
        self.ready = False
        for message_id in list(self._pending):
            self._reject(message_id, RuntimeError(reason))

    def snapshot(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "ready": self.ready,
            "reason": self.reason,
            "server_info": self.server_info,
            "tools": self.tools.snapshot(),
        }


def _iter_descriptors(descriptors: Any):
    """Yield ``(device_name, spec)`` for either descriptor shape."""
    if isinstance(descriptors, dict):
        for name, spec in descriptors.items():
            if isinstance(spec, dict):
                yield str(name), spec
        return
    if isinstance(descriptors, list):
        for item in descriptors:
            if not isinstance(item, dict):
                continue
            if "name" in item:
                yield str(item.get("name", "")), item
            else:
                for name, spec in item.items():
                    if isinstance(spec, dict):
                        yield str(name), spec


def _json_type(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value in {"number", "integer", "int", "float"}:
        return "integer" if value in {"integer", "int"} else "number"
    if value == "boolean":
        return "boolean"
    return "string"


def _as_arguments(arguments: Any) -> dict:
    if arguments is None or arguments == "":
        return {}
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except (TypeError, ValueError):
            raise ValueError(f"tool_arguments_not_json:{arguments[:80]}") from None
        if isinstance(parsed, dict):
            return parsed
        raise ValueError("tool_arguments_must_be_object")
    raise ValueError(f"tool_arguments_type:{type(arguments).__name__}")


def _flatten_result(result: Any) -> str:
    """Mirror the upstream flattening: prefer the first text content block."""
    if isinstance(result, dict):
        if result.get("isError") is True:
            raise RuntimeError(f"tool_error:{result.get('error') or 'unknown'}")
        content = result.get("content")
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict) and "text" in first:
                return str(first["text"])
        if "structuredContent" in result:
            return json.dumps(result["structuredContent"], ensure_ascii=False)
        return json.dumps({k: v for k, v in result.items() if k != "content"}, ensure_ascii=False)[:2000]
    return str(result)
