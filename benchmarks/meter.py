"""Meter every agent's model calls the same way.

A pass-through for the OpenAI API that the benchmark puts between each agent and the provider:

    agent in its sandbox  ->  http://host.docker.internal:PORT/<tag>/v1/...  ->  https://api.openai.com/v1/...

It exists so that token counts come from the provider's own usage figures for every agent, not
from each agent's bookkeeping, and so the real API key never enters a sandbox: agents are given
a throwaway token, and this process swaps in the key. It also enforces a spending limit.

What it changes in a request, and nothing else:

* the Authorization header;
* ``stream_options.include_usage`` on streamed chat completions, so usage is reported. When the
  client did not ask for it, the extra usage-only chunk is removed again before it reaches the client;
* with ``--effort``, a request to the benchmark model that states no reasoning effort is given that
  effort, and ``temperature``/``top_p`` are dropped from it because the API refuses them alongside
  reasoning. A request that states an effort is left alone.

Rate-limit and server errors from the provider are retried here before the agent sees them, so an
agent's score does not depend on how busy the shared key was.

Requires aiohttp. The key is read from OPENAI_API_KEY and never logged.
"""

import argparse
import asyncio
import json
import os
import time

from aiohttp import ClientSession, ClientTimeout, TCPConnector, web

UPSTREAM = "https://api.openai.com/v1/"
# USD per million tokens: uncached input, cached input, output.
PRICES = {"gpt-5.1": (1.25, 0.125, 10.0),
          # OpenCode names its sessions with this small model. Its cached rate is not listed, so cached input is charged in full.
          "gpt-5.4-nano": (0.2, 0.2, 1.25)}
HOP = {"host", "authorization", "content-length", "accept-encoding", "connection", "api-key", "transfer-encoding",
       "content-encoding", "keep-alive"}
RETRY = {429, 500, 502, 503, 504}


def price(model):
    for name, rates in PRICES.items():
        if (model or "").startswith(name):
            return rates
    return None


def counts(usage):
    """Return (input, cached input, output, reasoning) from a chat-completions or responses usage object."""
    if "prompt_tokens" in usage:
        details, out = usage.get("prompt_tokens_details") or {}, usage.get("completion_tokens_details") or {}
        return (usage.get("prompt_tokens") or 0, details.get("cached_tokens") or 0, usage.get("completion_tokens") or 0,
                out.get("reasoning_tokens") or 0)
    details, out = usage.get("input_tokens_details") or {}, usage.get("output_tokens_details") or {}
    return (usage.get("input_tokens") or 0, details.get("cached_tokens") or 0, usage.get("output_tokens") or 0,
            out.get("reasoning_tokens") or 0)


class Meter:
    def __init__(self, options):
        self.options = options
        self.key = os.environ["OPENAI_API_KEY"]
        self.spent = 0.0
        self.tags = {}
        if os.path.exists(options.ledger):  # a restart keeps what was already spent
            with open(options.ledger, encoding="utf-8") as stream:
                for line in stream:
                    entry = json.loads(line)
                    row = self.row(entry["tag"])
                    row["requests"] += 1
                    if "usd" in entry:
                        row["metered"] += 1
                        for name in ("input", "cached", "output", "reasoning", "usd"):
                            row[name] += entry[name]
                        self.spent += entry["usd"]
                    elif entry.get("status", 0) >= 400:
                        row["errors"] += 1
                    else:
                        row["unmetered"] += 1
        self.ledger = open(options.ledger, "a", encoding="utf-8")
        self.session = None

    def row(self, tag):
        return self.tags.setdefault(tag, {"requests": 0, "metered": 0, "unmetered": 0, "errors": 0, "retries": 0, "input": 0,
                                          "cached": 0, "output": 0, "reasoning": 0, "usd": 0.0, "unpriced": 0, "models": {}})

    def record(self, tag, kind, model, status, usage, effort, started):
        row = self.row(tag)
        entry = {"time": round(time.time(), 3), "tag": tag, "endpoint": kind, "model": model, "status": status,
                 "effort": effort, "seconds": round(time.time() - started, 3)}
        row["models"][model or "?"] = row["models"].get(model or "?", 0) + 1
        if usage:
            given, cached, output, reasoning = counts(usage)
            rates = price(model)
            cost = ((given - cached) * rates[0] + cached * rates[1] + output * rates[2]) / 1e6 if rates else 0.0
            row["metered"] += 1
            row["unpriced"] += 0 if rates else 1
            for name, value in (("input", given), ("cached", cached), ("output", output), ("reasoning", reasoning), ("usd", cost)):
                row[name] += value
            self.spent += cost
            entry.update(input=given, cached=cached, output=output, reasoning=reasoning, usd=round(cost, 6))
        elif status < 400:
            row["unmetered"] += 1
        else:
            row["errors"] += 1
        self.ledger.write(json.dumps(entry) + "\n")
        self.ledger.flush()

    async def stats(self, request):
        return web.json_response({"spent_usd": round(self.spent, 4), "budget_usd": self.options.budget, "tags": self.tags})

    async def handle(self, request):
        tag, tail = request.match_info["tag"], request.match_info["tail"]
        if request.headers.get("Authorization") != "Bearer " + self.options.token:
            return web.json_response({"error": {"message": "This endpoint needs the benchmark token.", "type": "invalid_request_error"}},
                                     status=401)
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return web.json_response({"error": {"message": "WebSocket transport is not available here.", "type": "invalid_request_error"}},
                                     status=404)
        row = self.row(tag)
        if self.spent >= self.options.budget or row["usd"] >= self.options.trial_budget:
            scope = "benchmark" if self.spent >= self.options.budget else "trial"
            row["errors"] += 1
            self.ledger.write(json.dumps({"time": round(time.time(), 3), "tag": tag, "status": 429, "refused": scope}) + "\n")
            self.ledger.flush()
            return web.json_response({"error": {"message": f"The {scope} spending limit is reached.", "type": "insufficient_quota",
                                                "code": "insufficient_quota"}}, status=429)
        raw = await request.read()
        kind = "chat" if tail.endswith("chat/completions") else "responses" if tail.endswith("responses") else "other"
        model, effort, stream, injected = None, None, False, False
        if raw and kind != "other":
            try:
                body = json.loads(raw)
            except ValueError:
                body = None
            if isinstance(body, dict):
                model, stream = body.get("model"), body.get("stream") is True
                reasoning = body.get("reasoning") if isinstance(body.get("reasoning"), dict) else {}
                effort = body.get("reasoning_effort") if kind == "chat" else reasoning.get("effort")
                if self.options.effort and not effort and (model or "").startswith(self.options.model):
                    effort = self.options.effort
                    if kind == "chat":
                        body["reasoning_effort"] = effort
                    else:
                        body["reasoning"] = {**reasoning, "effort": effort}
                    body.pop("temperature", None)
                    body.pop("top_p", None)
                if kind == "chat" and stream and not (body.get("stream_options") or {}).get("include_usage"):
                    body["stream_options"] = {**(body.get("stream_options") or {}), "include_usage": True}
                    injected = True
                raw = json.dumps(body).encode()
        headers = {name: value for name, value in request.headers.items() if name.lower() not in HOP}
        headers["Authorization"] = "Bearer " + self.key
        headers["Accept-Encoding"] = "identity"
        url = UPSTREAM + tail + ("?" + request.query_string if request.query_string else "")
        row["requests"] += 1
        started = time.time()
        upstream = None
        for attempt in range(7):
            try:
                upstream = await self.session.request(request.method, url, headers=headers, data=raw or None)
            except (TimeoutError, OSError) as error:
                if attempt == 6:
                    self.record(tag, kind, model, 502, None, effort, started)
                    return web.json_response({"error": {"message": f"The provider could not be reached: {type(error).__name__}",
                                                        "type": "server_error"}}, status=502)
                row["retries"] += 1
                await asyncio.sleep(min(30, 2 ** attempt))
                continue
            if upstream.status not in RETRY or attempt == 6:
                break
            text = await upstream.text()
            upstream.release()
            if "insufficient_quota" in text:
                self.record(tag, kind, model, 429, None, effort, started)
                return web.Response(text=text, status=429, content_type="application/json")
            row["retries"] += 1
            try:
                wait = float(upstream.headers.get("retry-after", ""))
            except ValueError:
                wait = 2 ** attempt
            await asyncio.sleep(min(30, max(1, wait)))
        passed = {name: value for name, value in upstream.headers.items() if name.lower() not in HOP}
        if "text/event-stream" not in upstream.headers.get("Content-Type", ""):
            data = await upstream.read()
            usage = None
            if kind != "other" and upstream.status < 400:
                try:
                    usage = json.loads(data).get("usage")
                except (ValueError, AttributeError):
                    usage = None
            if kind != "other" or upstream.status >= 400:
                self.record(tag, kind, model, upstream.status, usage, effort, started)
            return web.Response(body=data, status=upstream.status, headers=passed)
        response = web.StreamResponse(status=upstream.status, headers=passed)
        usage, buffer = None, b""
        try:
            await response.prepare(request)
            async for chunk in upstream.content.iter_any():
                buffer += chunk.replace(b"\r\n", b"\n")
                while b"\n\n" in buffer:
                    event, buffer = buffer.split(b"\n\n", 1)
                    keep = True
                    for line in event.split(b"\n"):
                        if not line.startswith(b"data:") or line[5:].strip() == b"[DONE]":
                            continue
                        try:
                            item = json.loads(line[5:])
                        except ValueError:
                            continue
                        if kind == "chat" and item.get("usage"):
                            usage = item["usage"]
                            keep = not (injected and not item.get("choices"))
                        elif kind == "responses" and item.get("type") in ("response.completed", "response.incomplete", "response.failed"):
                            usage = (item.get("response") or {}).get("usage") or usage
                    if keep:
                        await response.write(event + b"\n\n")
            if buffer:
                await response.write(buffer)
            await response.write_eof()
        except (ConnectionError, asyncio.CancelledError, OSError):
            upstream.close()
            self.record(tag, kind, model, 499, usage, effort, started)
            raise
        self.record(tag, kind, model, upstream.status, usage, effort, started)
        return response


async def serve(options):
    meter = Meter(options)
    meter.session = ClientSession(connector=TCPConnector(limit=96), timeout=ClientTimeout(total=None, sock_connect=30, sock_read=900),
                                  auto_decompress=False)
    app = web.Application(client_max_size=64 * 1024 * 1024)
    app.router.add_get("/_stats", meter.stats)
    app.router.add_route("*", "/{tag}/v1/{tail:.*}", meter.handle)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, options.host, options.port).start()
    print(f"metering on http://{options.host}:{options.port}  budget USD {options.budget}", flush=True)
    await asyncio.Event().wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8377)
    parser.add_argument("--token", required=True, help="the throwaway token agents present")
    parser.add_argument("--ledger", default="usage.jsonl")
    parser.add_argument("--budget", type=float, required=True, help="stop serving once this many USD are spent")
    parser.add_argument("--trial-budget", type=float, default=3.0, help="USD limit for one tag")
    parser.add_argument("--model", default="gpt-5.1", help="the benchmark model, for --effort")
    parser.add_argument("--effort", default="", help="reasoning effort given to requests that state none")
    asyncio.run(serve(parser.parse_args()))


if __name__ == "__main__":
    main()
