"""OpenRouter's loopback PKCE grant for a user-initiated terminal login."""

import base64
import hashlib
import json
import secrets
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer


def login():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)
    code = None

    class Callback(BaseHTTPRequestHandler):
        timeout = 3

        def do_GET(self):
            nonlocal code
            url = urllib.parse.urlsplit(self.path)
            params = urllib.parse.parse_qs(url.query)
            if url.path != "/callback":
                self.send_error(404)
                return
            if not secrets.compare_digest(params.get("state", [""])[0].encode(), state.encode()):
                # OpenRouter omits state when the user declines, so stop waiting.
                code = code or ""
                self.send_error(400)
                return
            code = params.get("code", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b"Signed in. You can close this tab and return to Turnloom.")

        def log_message(self, *args):
            pass

    with HTTPServer(("127.0.0.1", 0), Callback) as server:
        query = urllib.parse.urlencode({"callback_url": f"http://localhost:{server.server_port}/callback",
                                        "code_challenge": challenge, "code_challenge_method": "S256",
                                        "state": state, "key_label": "Turnloom"})
        url = "https://openrouter.ai/auth?" + query
        if not webbrowser.open(url):
            print(f"Open in a browser on this computer:\n{url}")
        server.timeout = 1
        deadline = time.monotonic() + 180
        while code is None and time.monotonic() < deadline:
            server.handle_request()
    if not code:
        raise ValueError("OpenRouter authorization was cancelled or expired.")
    payload = {"code": code, "code_verifier": verifier, "code_challenge_method": "S256"}
    request = urllib.request.Request("https://openrouter.ai/api/v1/auth/keys",
                                     data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        key = json.load(response).get("key")
    if not isinstance(key, str) or not key:
        raise ValueError("OpenRouter returned no API key.")
    return key
