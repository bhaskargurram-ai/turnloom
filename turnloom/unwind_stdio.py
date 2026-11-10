"""Run Unwind's reversibility proxy in front of an MCP server on Windows process streams.

    python -m turnloom.unwind_stdio LEDGER [COMMAND [ARGS...]]

With no command the upstream is the bundled VerifyDoc server.
"""

import asyncio
import sys


async def serve(ledger, *upstream):
    from unwind.proxy.stdio import UnwindStdioProxy

    if upstream:
        command, arguments = upstream[0], list(upstream[1:])
    elif getattr(sys, "frozen", False):
        command, arguments = sys.executable, ["--internal", "verifydoc"]
    else:
        command, arguments = sys.executable, ["-I", "-m", "verifydoc.mcp_server"]
    proxy = UnwindStdioProxy(command, arguments, db_path=ledger)
    # Unwind 0.2 accepts caller-provided streams on its pump. Its default
    # Unix pipe adapter rejects Windows anonymous pipe handles.
    pump = proxy._pump

    class Reader:
        async def readline(self):
            data = await asyncio.to_thread(sys.stdin.buffer.readline)
            if not data and pump._proc and pump._proc.stdin:
                pump._proc.stdin.close()
            return data

    class Writer:
        def write(self, data):
            sys.stdout.buffer.write(data)

        async def drain(self):
            sys.stdout.buffer.flush()

    return await pump.run(Reader(), Writer())


if __name__ == "__main__":
    raise SystemExit(asyncio.run(serve(*sys.argv[1:])))
