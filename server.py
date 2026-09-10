#!/usr/bin/env python3
"""ENGINE 3 TEST (discovery pass 2026-09-10): LLM-surface retrofit. One service, two faces:

  1. An MCP server (streamable HTTP, JSON responses) at /mcp exposing three tools that wrap
     things we already run. The bet, receipted twice in the discovery pass (SocialKit buyers
     arriving from ChatGPT/Perplexity, Fudge's MCP distribution): assistants recommending
     tools is a real acquisition channel that costs nothing to stand in.
  2. Free tool pages (/tools/audit, /tools/flip) so the same capabilities are also plain web
     pages that search engines and LLMs can cite.

Every hit logs user-agent + referer + MCP client name to ~/mcp_hits.db so directory-sourced
traffic is COUNTABLE (that is the test's kill metric).

Tools:
  deprecation_check     wraps rescue_service.scan_text (Assistants API, Content API, etc.)
  website_audit         wraps tools/field/site_audit.audit(url), verdicts like a human would
  flipworth_resale_scan submits an image URL to the live FlipWorth backend; free tier is
                        metered per device, the 402 surfaces the upgrade link (the funnel)

Runs at 127.0.0.1:8793 behind nginx on sites.silentdirectivellc.com (/mcp, /tools/*).
"""
import base64, html, json, os, re, sqlite3, sys, time, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOME = os.path.expanduser("~")
sys.path.insert(0, HOME)
sys.path.insert(0, os.path.join(HOME, "repo", "tools", "field"))
import rescue_service            # scan_text, fetch_url, CHECKS
import site_audit                # audit(url)

DB = os.path.join(HOME, "mcp_hits.db")
FLIP = "https://flipworth.silentdirectivellc.com"
PROTO = "2025-06-18"

TOOLS = [
    {"name": "deprecation_check",
     "description": ("Check code, a requirements/package list, or a URL for API surfaces "
                     "that were shut down or deprecated in 2026: OpenAI Assistants API "
                     "(removed Aug 26), Google Content API for Shopping (retired Aug 18), "
                     "Relay.app (shutting down), dead model snapshots, OpenAI v0 SDK "
                     "patterns. Returns each hit with severity and the migration path."),
     "inputSchema": {"type": "object", "properties": {
         "input": {"type": "string",
                   "description": "Source code, a dependency list, or an http(s) URL to fetch and scan"}},
         "required": ["input"]}},
    {"name": "website_audit",
     "description": ("Audit a small-business website the way a door-to-door web designer "
                     "would: does it load, is it https, mobile viewport, page weight, dead "
                     "or parked domain, social page standing in for a real site. Returns a "
                     "verdict (NO_SITE, SOCIAL_ONLY, BAD_SITE, DEAD_SITE, GOOD_SITE) and notes."),
     "inputSchema": {"type": "object", "properties": {
         "url": {"type": "string", "description": "The website URL to audit"}},
         "required": ["url"]}},
    {"name": "flipworth_resale_scan",
     "description": ("Estimate what a secondhand item is worth to resell. Give an image URL "
                     "of the item (thrift find, garage sale, shelf photo); returns the "
                     "FlipWorth vision engine's read: what it is, resale range, sell-through "
                     "signals. Free scans are limited per client; past the limit the "
                     "response includes the upgrade link."),
     "inputSchema": {"type": "object", "properties": {
         "image_url": {"type": "string", "description": "Public URL of a photo of the item"},
         "category": {"type": "string", "description": "Optional hint: thrift, electronics, clothing, toys, media"}},
         "required": ["image_url"]}},
]


def db():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS hits(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, path TEXT, tool TEXT,
        ua TEXT, referer TEXT, client TEXT)""")
    c.commit()
    return c


def log_hit(path, tool, headers, client=""):
    try:
        c = db()
        c.execute("INSERT INTO hits(ts,path,tool,ua,referer,client) VALUES(?,?,?,?,?,?)",
                  (time.time(), path, tool, headers.get("User-Agent", "")[:300],
                   headers.get("Referer", "")[:300], client[:120]))
        c.commit()
    except Exception:
        pass


def esc(s):
    return html.escape(str(s or ""), quote=True)


# ── tool implementations ─────────────────────────────────────────────────────
def t_deprecation_check(args):
    text = (args.get("input") or "").strip()
    if not text:
        return "give me code, a requirements list, or a URL"
    if re.match(r"^https?://\S+$", text):
        body, err = rescue_service.fetch_url(text)
        if err:
            return "could not fetch that URL: " + err
        text = body
    found = rescue_service.scan_text(text)
    if not found:
        return ("No deprecated surfaces matched. Covered: OpenAI Assistants API, Google "
                "Content API for Shopping, Relay.app, dead model snapshots, v0 SDK patterns.")
    lines = []
    for f in found:
        label = {"dead": "BROKEN NOW", "dying": "SHUTTING DOWN", "warn": "DEPRECATED"}[f["sev"]]
        lines.append("{}: {} Fix: {} (matched {}x)".format(label, f["what"], f["fix"], f["count"]))
    lines.append("Full interactive checker: https://sites.silentdirectivellc.com/scan")
    return "\n".join(lines)


def t_website_audit(args):
    url = (args.get("url") or "").strip()
    if not url:
        return "give me a URL"
    if not url.startswith("http"):
        url = "https://" + url
    verdict, notes, ms, weight = site_audit.audit(url)
    out = "verdict: {}\n".format(verdict)
    if notes:
        out += "notes: " + "; ".join(notes) + "\n"
    if ms:
        out += "load: {} ms, {} KB\n".format(ms, round(weight / 1024) if weight else "?")
    return out


def t_flip_scan(args, client):
    img = (args.get("image_url") or "").strip()
    if not re.match(r"^https?://", img):
        return "image_url must be a public http(s) URL of a photo"
    try:
        with urllib.request.urlopen(urllib.request.Request(
                img, headers={"User-Agent": "flipworth-mcp/1.0"}), timeout=15) as r:
            raw = r.read(4 * 1024 * 1024)
            mime = r.headers.get("Content-Type", "image/jpeg").split(";")[0]
    except Exception as e:
        return "could not fetch the image: " + str(e)[:100]
    if not mime.startswith("image/"):
        return "that URL is not an image (got {})".format(mime)
    # The engine 403s device ids that do not look like real device strings. Deterministic
    # uuid5 per MCP client keeps the free-scan meter honest (same client = same device),
    # verified against the live gate 2026-09-10.
    import uuid as _uuid
    device = "asc-mcp-" + str(_uuid.uuid5(_uuid.NAMESPACE_DNS, (client or "anon").lower()))
    body = json.dumps({"image": base64.b64encode(raw).decode(), "mime": mime,
                       "mode": "source", "category": args.get("category") or "thrift",
                       "context": "mcp tool call", "device": device}).encode()
    try:
        req = urllib.request.Request(FLIP + "/api/flipscore/score_job", data=body,
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "flipworth-mcp/1.0"})
        d = json.load(urllib.request.urlopen(req, timeout=60))
    except urllib.error.HTTPError as e:
        if e.code == 402:
            return ("Free scans for this client are used up. Unlimited scans come with the "
                    "FlipWorth pass: https://flipworth.silentdirectivellc.com")
        return "scan submit failed: HTTP {}".format(e.code)
    except Exception as e:
        return "scan submit failed: " + str(e)[:100]
    job = d.get("job_id")
    if not job:
        return "backend accepted nothing: " + json.dumps(d)[:200]
    deadline = time.time() + 90
    while time.time() < deadline:
        time.sleep(4)
        try:
            s = json.load(urllib.request.urlopen(urllib.request.Request(
                FLIP + "/api/flipscore/job/" + job,
                headers={"User-Agent": "flipworth-mcp/1.0"}), timeout=30))
        except Exception:
            continue
        if s.get("status") == "done":
            r = s.get("result") or s
            return json.dumps(r, indent=1)[:1800]
        if s.get("status") == "error":
            return "scan errored: " + str(s.get("error"))[:200]
    return "scan still running after 90s, try again shortly (job {})".format(job)


# ── minimal MCP streamable-http endpoint (JSON responses, no SSE stream) ─────
def mcp_handle(msg, headers):
    mid = msg.get("id")
    method = msg.get("method", "")
    if method == "initialize":
        client = ((msg.get("params") or {}).get("clientInfo") or {}).get("name", "")
        log_hit("/mcp", "initialize", headers, client)
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": (msg.get("params") or {}).get("protocolVersion", PROTO),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "silentdirective-tools", "version": "1.0.0"}}}
    if method.startswith("notifications/"):
        return None
    if method == "tools/list":
        log_hit("/mcp", "tools/list", headers)
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        p = msg.get("params") or {}
        name = p.get("name", "")
        args = p.get("arguments") or {}
        client = headers.get("Mcp-Client", "") or headers.get("User-Agent", "")[:40]
        log_hit("/mcp", name, headers, client)
        try:
            if name == "deprecation_check":
                text = t_deprecation_check(args)
            elif name == "website_audit":
                text = t_website_audit(args)
            elif name == "flipworth_resale_scan":
                text = t_flip_scan(args, client)
            else:
                return {"jsonrpc": "2.0", "id": mid,
                        "error": {"code": -32602, "message": "unknown tool " + name}}
            return {"jsonrpc": "2.0", "id": mid,
                    "result": {"content": [{"type": "text", "text": text}]}}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32603, "message": str(e)[:200]}}
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": "method not supported: " + method}}


# ── the free tool pages ──────────────────────────────────────────────────────
CSS = rescue_service.CSS

AUDIT_PAGE = """
<h1>Is this website costing them customers?</h1>
<div class="sub">Paste any small-business website. Same checks a door-to-door web designer
runs: does it load, is it secure, does it work on a phone, is it a Facebook page pretending
to be a website. Free, no signup.</div>
<form method="POST" action="/tools/audit">
<input name="url" placeholder="https://joessmogshop.com" required>
<button>Audit it</button>
</form>
<p class="fine">Also available as an MCP tool for AI assistants: connect
https://sites.silentdirectivellc.com/mcp and call website_audit.</p>"""

FLIP_PAGE = """
<h1>What is this worth to resell?</h1>
<div class="sub">Paste a public image URL of the item (a thrift find, a shelf photo). The
FlipWorth vision engine reads what it is and what it resells for. A few free scans per
visitor, unlimited with the app.</div>
<form method="POST" action="/tools/flip">
<input name="image_url" placeholder="https://.../photo.jpg" required>
<button>Scan it</button>
</form>
<p class="fine">iOS app: FlipWorth on the App Store. MCP tool for AI assistants:
https://sites.silentdirectivellc.com/mcp, tool flipworth_resale_scan.</p>"""


def tool_page(title, inner):
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>" + esc(title) + "</title><style>" + CSS + "</style></head><body>"
            + inner + "</body></html>")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _s(self, code, body, ctype="text/html; charset=utf-8"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/tools/audit":
            log_hit(u.path, "page", self.headers)
            return self._s(200, tool_page("Website audit", AUDIT_PAGE))
        if u.path == "/tools/flip":
            log_hit(u.path, "page", self.headers)
            return self._s(200, tool_page("Resale scan", FLIP_PAGE))
        if u.path == "/mcp":
            return self._s(405, json.dumps({"error": "POST JSON-RPC here; no SSE stream"}),
                           "application/json")
        return self._s(404, tool_page("Not found", "<h1>Not found.</h1>"))

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(min(n, 2 * 1024 * 1024))

        if u.path == "/mcp":
            try:
                msg = json.loads(raw.decode("utf-8", "replace"))
            except Exception:
                return self._s(400, json.dumps({"jsonrpc": "2.0", "id": None,
                               "error": {"code": -32700, "message": "parse error"}}),
                               "application/json")
            if isinstance(msg, list):
                out = [r for r in (mcp_handle(m, self.headers) for m in msg) if r]
                return self._s(200, json.dumps(out), "application/json")
            r = mcp_handle(msg, self.headers)
            if r is None:
                return self._s(202, "", "application/json")
            return self._s(200, json.dumps(r), "application/json")

        f = urllib.parse.parse_qs(raw.decode("utf-8", "replace"))
        if u.path == "/tools/audit":
            log_hit(u.path, "run", self.headers)
            out = t_website_audit({"url": f.get("url", [""])[0]})
            return self._s(200, tool_page("Audit report",
                "<h1>Audit report</h1><div class=\"sub\"><a href=\"/tools/audit\">run another"
                "</a></div><div class=\"card\"><p style=\"white-space:pre-wrap\">" + esc(out) +
                "</p></div><p class=\"fine\">A site that fails these checks loses walk-in "
                "customers every day. We build and host small-business sites, and the checks "
                "above are exactly what we fix.</p>"))
        if u.path == "/tools/flip":
            log_hit(u.path, "run", self.headers)
            out = t_flip_scan({"image_url": f.get("image_url", [""])[0]}, "webpage")
            return self._s(200, tool_page("Scan result",
                "<h1>Scan result</h1><div class=\"sub\"><a href=\"/tools/flip\">scan another"
                "</a></div><div class=\"card\"><p style=\"white-space:pre-wrap\">" + esc(out) +
                "</p></div>"))
        return self._s(404, tool_page("Not found", "<h1>Not found.</h1>"))


def main():
    port = int(os.environ.get("PORT", "8793"))
    print("mcp+tools on 127.0.0.1:{}".format(port), flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    main()
