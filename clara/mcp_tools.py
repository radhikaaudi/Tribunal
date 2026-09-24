"""
TigerGraph MCP bridge: spawns the official TigerGraph MCP server (`tigergraph-mcp`,
package `pyTigerGraph-mcp`) over stdio with the `mcp` Python SDK and exposes it as
plain sync calls for the investigator.

    from clara import mcp_tools
    m = mcp_tools.get()                     # None if TG unconfigured / server won't start
    if m:
        m.list_tools()                      # ['tigergraph__run_installed_query', ...]
        m.run_installed_query("device_neighbors",
                              {"profile": "...", "t0": "2016-11-01 00:00:00", "t1": "2016-12-31 23:59:59"})
        m.call("tigergraph__get_vertex_count", {"vertex_type": "Transaction"})

Never raises; failures return None and set `last_error`. `calls` counts tool calls.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import threading
from contextlib import AsyncExitStack
from typing import Any, Optional

from . import tg as _tg

START_TIMEOUT = float(os.getenv("TG_MCP_START_TIMEOUT", "25"))
CALL_TIMEOUT = float(os.getenv("TG_MCP_CALL_TIMEOUT", "60"))

_instance: Optional["MCPTools"] = None
_failed: Optional[str] = None
_lock = threading.Lock()


def server_command() -> list[str]:
    exe = os.path.join(os.path.dirname(sys.executable), "tigergraph-mcp")
    if os.path.exists(exe):
        return [exe]
    found = shutil.which("tigergraph-mcp")
    return [found] if found else [sys.executable, "-m", "tigergraph_mcp.main"]


def server_env() -> Optional[dict]:
    """Env for the MCP server, normalised from our TG_* config (ports/tgcloud for Savanna)."""
    cfg = _tg._cfg()
    if cfg is None:
        return None
    env = dict(os.environ)
    env.update({
        "TG_HOST": cfg["host"], "TG_GRAPHNAME": cfg["graphname"],
        "TG_USERNAME": cfg["username"], "TG_PASSWORD": cfg["password"],
        "TG_SECRET": cfg["gsqlSecret"], "TG_RESTPP_PORT": str(cfg["restppPort"]),
        "TG_GS_PORT": str(cfg["gsPort"]), "TG_TGCLOUD": "true" if cfg["tgCloud"] else "false",
    })
    return env


def _unwrap(res: Any) -> Any:
    """CallToolResult -> parsed JSON (or text)."""
    if res is None:
        return None
    sc = getattr(res, "structuredContent", None) or getattr(res, "structured_content", None)
    if sc:
        return sc
    texts = [getattr(c, "text", "") for c in (getattr(res, "content", None) or []) if getattr(c, "text", None)]
    out = "\n".join(texts).strip()
    if out.startswith("```"):
        out = out.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(out)
    except Exception:
        return out


class MCPTools:
    def __init__(self):
        self.calls = 0
        self.last_error: Optional[str] = None
        self.tools: list[str] = []
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True, name="tg-mcp")
        self._thread.start()
        self._stack: Optional[AsyncExitStack] = None
        self._session = None

    def _run(self, coro, timeout):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    async def _astart(self, env: dict):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        cmd = server_command()
        params = StdioServerParameters(command=cmd[0], args=cmd[1:], env=env, cwd=_tg.ROOT)
        self._stack = AsyncExitStack()
        # server logs (deprecation notices, token warnings) go to a file, not the analyst's terminal
        os.makedirs(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory"), exist_ok=True)
        self._errlog = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory", "mcp_server.log"), "a")
        read, write = await self._stack.enter_async_context(stdio_client(params, errlog=self._errlog))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        lst = await self._session.list_tools()
        self.tools = [t.name for t in lst.tools]

    def start(self, env: dict) -> bool:
        try:
            self._run(self._astart(env), START_TIMEOUT)
            return True
        except Exception as e:
            self.last_error = f"start: {type(e).__name__}: {e}"
            return False

    def list_tools(self) -> list[str]:
        return list(self.tools)

    def call(self, tool_name: str, args: Optional[dict] = None) -> Any:
        self.calls += 1
        try:
            res = self._run(self._session.call_tool(tool_name, args or {}), CALL_TIMEOUT)
            if getattr(res, "isError", False) or getattr(res, "is_error", False):
                self.last_error = f"{tool_name}: {_unwrap(res)}"
            return _unwrap(res)
        except Exception as e:
            self.last_error = f"{tool_name}: {type(e).__name__}: {e}"
            return None

    def run_installed_query(self, name: str, params: Optional[dict] = None) -> Any:
        return self.call("tigergraph__run_installed_query",
                         {"query_name": name, "params": params or {}})

    def run_query(self, query_text: str) -> Any:
        """Interpreted GSQL/openCypher; text must include the INTERPRET wrapper."""
        return self.call("tigergraph__run_query", {"query_text": query_text})

    def close(self):
        try:
            if self._stack:
                self._run(self._stack.aclose(), 10)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)


def get() -> Optional[MCPTools]:
    """Singleton MCP client, or None when TG is unconfigured/unreachable or the server fails."""
    global _instance, _failed
    if _instance is not None or _failed is not None:
        return _instance
    with _lock:
        if _instance is not None or _failed is not None:
            return _instance
        cfg = _tg._cfg()
        if cfg is None:
            _failed = "unconfigured"
            return None
        if not _tg._ping(cfg):
            _failed = "unreachable"
            return None
        m = MCPTools()
        if not m.start(server_env()):
            _failed = m.last_error
            m.close()
            return None
        _instance = m
    return _instance


def failure_reason() -> Optional[str]:
    return _failed


def serve() -> None:
    """Run the official TigerGraph MCP server in-process with normalised TG_* env
    (used by .mcp.json: `python -m clara.mcp_tools --serve`)."""
    env = server_env()
    if env:
        os.environ.update(env)
    from tigergraph_mcp.main import main as _main
    sys.argv = [sys.argv[0]]
    _main()


if __name__ == "__main__" and "--serve" in sys.argv:
    serve()
elif __name__ == "__main__":
    m = get()
    if not m:
        print("MCP unavailable:", failure_reason())
    else:
        print(len(m.tools), "tools:", ", ".join(m.tools))
        print(m.call("tigergraph__get_vertex_count", {}))
        m.close()
