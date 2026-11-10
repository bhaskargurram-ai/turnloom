"""Supply Windows process streams to the pinned Unwind proxy's transport."""

import asyncio
import sys


async def serve(ledger, upstream):
    from unwind.proxy.stdio import UnwindStdioProxy

    proxy = UnwindStdioProxy(upstream, db_path=ledger)
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
