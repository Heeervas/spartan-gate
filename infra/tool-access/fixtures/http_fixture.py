"""Synthetic HTML/search only; no external requests or persistent browser data."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

HTML = b'''<!doctype html><meta charset="utf-8"><title>Spartan isolated fixture</title>
<h1>Isolated managed browser fixture</h1>
<label for="fixture-input">Fixture input</label><input id="fixture-input">
<button id="fixture-button" onclick="document.getElementById('result').textContent='Clicked once'">Fixture button</button>
<p id="result">Not clicked</p><div style="height:2200px">Scroll fixture</div><p>Fixture end</p>'''
COUNTS = {"search": 0, "html": 0, "redirect": 0}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path == "/counts":
            self.reply(200, json.dumps(COUNTS).encode(), "application/json")
        elif parsed.path == "/search":
            COUNTS["search"] += 1
            query = parse_qs(parsed.query).get("q", [""])[0]
            payload = {"query": query, "results": [{"title": "Synthetic search result",
                       "url": "http://fixture:8080/page", "content": "Synthetic fixture content"}]}
            self.reply(200, json.dumps(payload).encode(), "application/json")
        elif parsed.path == "/redirect-private":
            COUNTS["redirect"] += 1
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif parsed.path == "/page":
            COUNTS["html"] += 1
            self.reply(200, HTML, "text/html; charset=utf-8")
        else:
            self.reply(404, b"Not found", "text/plain")

    def reply(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
