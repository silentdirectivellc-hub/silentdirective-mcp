# silentdirective-tools MCP server

Three practical tools for AI assistants, served over MCP streamable HTTP. No signup, no key.

**Endpoint: `https://sites.silentdirectivellc.com/mcp`**

| tool | what it does |
|---|---|
| `deprecation_check` | Scans code, dependency lists, or a URL for 2026 API shutdowns: OpenAI Assistants API (removed Aug 26), Google Content API for Shopping (retired Aug 18), Relay.app, dead model snapshots, v0 SDK patterns. Returns severity + migration path per hit. |
| `website_audit` | Audits a small-business website like a professional would: loads, https, mobile viewport, weight, parked/dead domain, social-page-as-site. Verdict + notes. |
| `flipworth_resale_scan` | Photo URL of a secondhand item in, resale read out (what it is, what it sells for), powered by the FlipWorth vision engine. |

## Connect

Claude Code:
```
claude mcp add silentdirective-tools --transport http https://sites.silentdirectivellc.com/mcp
```
Any MCP client supporting streamable HTTP: point it at the endpoint above. POST JSON-RPC
(`initialize`, `tools/list`, `tools/call`); responses are plain JSON.

## Web versions
The same tools as free pages: [/scan](https://sites.silentdirectivellc.com/scan),
[/tools/audit](https://sites.silentdirectivellc.com/tools/audit),
[/tools/flip](https://sites.silentdirectivellc.com/tools/flip).

## Run it yourself
`server.py` is a single-file Python 3 stdlib server (no dependencies). Two imports
(`rescue_service`, `site_audit`) carry the check logic; see this repo. MIT license.
