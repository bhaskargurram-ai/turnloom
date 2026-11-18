"""Turnloom as a layer under another harness.

    turnloom proxy --port 8787

An OpenAI-compatible chat-completions endpoint on loopback. Point any harness at
http://127.0.0.1:8787/v1 and each request is forwarded to your connected model with
older long tool results folded into previews. The model gets a ``turnloom_recall``
tool to read a folded result back; the proxy answers those calls itself, so the
harness never sees them. Every call lands in the workspace usage ledger.
"""

import copy
import json
import secrets
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

from turnloom import __version__, gateway, meter
from turnloom.context import Observations

RECALL = "turnloom_recall"
RECALL_TOOL = {"type": "function", "function": {
    "name": RECALL, "description": "Read an older tool result that was folded into a preview. Pass its reference with "
    "a search string to get the matching lines, or an offset and length (up to 4000) to read a slice.",
    "parameters": {"type": "object", "required": ["reference"], "properties": {
        "reference": {"type": "string"}, "offset": {"type": "integer"}, "length": {"type": "integer"},
        "search": {"type": "string", "description": "Literal text to find. Returns up to five matches with context."}}}}}
LIMIT = 64 * 1024 * 1024


class Layer:
    """Folding, recall and metering for one upstream connection."""

    def __init__(self, connection, workspace=None, keep=3, fold=True, send=None):
        self.connection = connection
        self.keep = keep
        self.fold = fold
        self.archive = Observations(capacity=20_000_000)
        self.lock = threading.Lock()
        self.ledger = meter.Ledger(workspace) if workspace else None
        self.send = send or self.forward
        self.totals = {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0,
                       "folded_chars": 0, "saved_tokens": 0, "recalls": 0, "cost": 0.0}

    def forward(self, body):
        connection = self.connection
        if connection.endpoint.startswith(gateway.SCHEME):
            return gateway.complete(body, connection.key, 600)
        url = connection.endpoint.rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                         headers={"Content-Type": "application/json", "User-Agent": "Turnloom-Proxy/" + __version__})
        if connection.key:
            request.add_unredirected_header("Authorization", "Bearer " + connection.key)
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise Upstream(error.code, error.read(20000)) from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise Upstream(502, json.dumps({"error": {"message": f"Upstream unreachable: {error}"}}).encode()) from error

    def complete(self, body, client=""):
        """Fold, forward, resolve recall calls locally, and return an OpenAI chat completion."""
        body = copy.deepcopy(body)
        body.pop("stream", None)
        body.pop("stream_options", None)
        original = copy.deepcopy(body.get("messages"))
        if not body.get("model") or body["model"] in ("turnloom", "default"):
            body["model"] = self.connection.model
        messages = body.get("messages")
        if not isinstance(messages, list):
            raise ValueError("messages must be an array")
        removed = 0
        with self.lock:
            if self.fold:
                removed = self.archive.fold(messages, self.keep, RECALL)
        folded = any(isinstance(m.get("content"), str) and m["content"].startswith('{"reference"') for m in messages
                     if isinstance(m, dict) and m.get("role") == "tool")
        if folded and isinstance(body.get("tools"), list) and body.get("tool_choice") in (None, "auto"):
            body["tools"] = [*body["tools"], RECALL_TOOL]
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0}
        recalls = 0
        def spend(response):
            reported = response.get("usage") or {}
            details = reported.get("prompt_tokens_details") or {}
            usage["prompt_tokens"] += int(reported.get("prompt_tokens") or 0)
            usage["completion_tokens"] += int(reported.get("completion_tokens") or 0)
            usage["cached_tokens"] += int(details.get("cached_tokens") or 0) if isinstance(details, dict) else 0

        for _ in range(8):
            response = self.send(body)
            spend(response)
            message = response["choices"][0]["message"]
            calls = message.get("tool_calls") or []
            mine = [call for call in calls if call.get("function", {}).get("name") == RECALL]
            if not mine:
                break
            # Answer recall here. Any other calls in the same reply are dropped unexecuted; with
            # the recalled text in hand the model issues them again.
            recalls += len(mine)
            messages.append({"role": "assistant", "content": message.get("content"), "tool_calls": mine})
            with self.lock:
                for call in mine:
                    messages.append({"role": "tool", "tool_call_id": call["id"],
                                     "content": self.archive.recall(call["function"].get("arguments") or "{}")})
        else:
            # The model kept reading. Correctness comes first: answer this request from the
            # caller's unfolded history, exactly as it would have been sent without the layer.
            body["messages"] = original
            body["tools"] = [tool for tool in body.get("tools") or [] if tool is not RECALL_TOOL]
            response = self.send(body)
            spend(response)
            removed = 0
        if isinstance(response.get("usage"), dict):
            response["usage"].update(prompt_tokens=usage["prompt_tokens"], completion_tokens=usage["completion_tokens"],
                                     total_tokens=usage["prompt_tokens"] + usage["completion_tokens"])
        connection = self.connection
        priced = bool(connection.input_rate or connection.output_rate or connection.free)
        cost = meter.price(usage, connection.input_rate, connection.output_rate, connection.cache_rate)
        saved = removed // 4
        with self.lock:
            self.totals["requests"] += 1
            self.totals["recalls"] += recalls
            self.totals["folded_chars"] += removed
            self.totals["saved_tokens"] += saved
            self.totals["cost"] += cost
            for key in usage:
                self.totals[key] += usage[key]
        if self.ledger:
            self.ledger.add(model=body["model"], persona="proxy" + (":" + client if client else ""), **usage,
                            cost=cost if priced else None, saved_tokens=saved,
                            saved_cost=saved * connection.input_rate / 1e6 if priced else 0)
        return response


class Upstream(Exception):
    def __init__(self, status, body):
        super().__init__(f"Upstream HTTP {status}")
        self.status, self.body = status, body


def stream_chunks(response):
    """Replay a finished completion as the server-sent events a streaming client expects."""
    choice = response["choices"][0]
    message = choice["message"]
    base = {"id": response.get("id") or "chatcmpl-" + uuid4().hex, "object": "chat.completion.chunk",
            "created": response.get("created") or int(time.time()), "model": response.get("model")}
    delta = {"role": "assistant", "content": message.get("content") or ""}
    for key in ("reasoning_content", "reasoning"):
        if message.get(key):
            delta[key] = message[key]
    if message.get("tool_calls"):
        delta["tool_calls"] = [{"index": index, "id": call.get("id"), "type": "function", "function": call["function"]}
                               for index, call in enumerate(message["tool_calls"])]
    yield {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
    yield {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": choice.get("finish_reason") or "stop"}]}
    if response.get("usage"):
        yield {**base, "choices": [], "usage": response["usage"]}


def make_server(layer, port=8787, token=""):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def reply(self, status, body, kind="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def allowed(self):
            # A harness on this machine sends no Origin and addresses the proxy by its loopback name.
            # Anything else is a web page trying to spend the operator's tokens, or a rebound hostname.
            port = self.server.server_port
            if self.headers.get("Origin") or self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
                self.reply(403, b'{"error":{"message":"The Turnloom proxy accepts local clients only"}}')
                return False
            supplied = self.headers.get("Authorization", "")
            if token and not secrets.compare_digest(supplied.encode(), ("Bearer " + token).encode()):
                self.reply(401, b'{"error":{"message":"Invalid proxy token"}}')
                return False
            return True

        def do_GET(self):
            if not self.allowed():
                return
            path = self.path.split("?")[0].rstrip("/")
            if path.endswith("/models"):
                self.reply(200, json.dumps({"object": "list", "data": [
                    {"id": layer.connection.model, "object": "model", "owned_by": "turnloom"}]}).encode())
            elif path.endswith("/turnloom/stats"):
                with layer.lock:
                    self.reply(200, json.dumps(layer.totals).encode())
            else:
                self.reply(404, b'{"error":{"message":"Not found"}}')

        def do_POST(self):
            if not self.allowed():
                return
            if not self.path.split("?")[0].rstrip("/").endswith("/chat/completions"):
                return self.reply(404, b'{"error":{"message":"Turnloom proxies /v1/chat/completions"}}')
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= LIMIT:
                    raise ValueError("Invalid request size")
                body = json.loads(self.rfile.read(length))
                agent = (self.headers.get("User-Agent") or "").split("/")[0][:40]
                response = layer.complete(body, agent)
            except Upstream as error:
                return self.reply(error.status, error.body)
            except (ValueError, KeyError, IndexError, TypeError, RuntimeError) as error:
                return self.reply(400, json.dumps({"error": {"message": str(error), "type": "invalid_request_error"}}).encode())
            if body.get("stream"):
                usage = (body.get("stream_options") or {}).get("include_usage")
                events = [chunk for chunk in stream_chunks(response) if chunk["choices"] or usage]
                data = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in events) + "data: [DONE]\n\n"
                self.reply(200, data.encode("utf-8"), "text/event-stream")
            else:
                self.reply(200, json.dumps(response).encode("utf-8"))

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(connection, workspace, port, keep, fold, token=""):
    layer = Layer(connection, workspace, keep, fold)
    server = make_server(layer, port, token)
    print(f"Turnloom proxy on http://127.0.0.1:{server.server_port}/v1, forwarding to {connection.model}\n"
          f"Folding {'keeps the newest ' + str(keep) + ' tool results' if fold else 'off'}. "
          "Set your harness's OpenAI base URL to the address above. Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        totals = layer.totals
        print(f"\n{totals['requests']} requests · {totals['prompt_tokens']:,} input / {totals['completion_tokens']:,} output tokens"
              f" · {totals['folded_chars']:,} characters folded · {totals['recalls']} recalls")
    return 0
