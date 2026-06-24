# fetch-mcp — Odysseus Plugin

Connects the [official MCP Fetch server](https://github.com/modelcontextprotocol/servers/tree/main/src/fetch)
to Odysseus via the plugin system.

## What it provides

The Fetch MCP server exposes a `fetch` tool that retrieves web pages and
converts them to Markdown for the AI agent. No API key required.

## Quick start

**1. Start the fetch MCP server** (requires Node.js ≥ 18):

```bash
npx -y @modelcontextprotocol/server-fetch --port 8181
```

The server listens on `http://localhost:8181`. Leave this terminal open.

**2. Configure the plugin in Odysseus**

Open Settings → Integrations → fetch-mcp and set:

| Field | Value |
|-------|-------|
| Server URL | `http://localhost:8181/mcp` |
| Auth Headers | *(leave blank — no auth needed)* |

Click **Save**, then **Test** to verify the connection.

**3. Enable the plugin**

Toggle the enable switch on the fetch-mcp card. The Fetch service should
show a green dot indicating it's connected.

## Running as a service (optional)

For persistent access, run the server as a background service:

```bash
# Linux / macOS (using pm2)
npx pm2 start "npx @modelcontextprotocol/server-fetch --port 8181" --name mcp-fetch

# Windows (PowerShell)
Start-Process npx -ArgumentList "@modelcontextprotocol/server-fetch", "--port", "8181" -WindowStyle Hidden
```

Or set `ODYSSEUS_MCP_FETCH_URL=http://localhost:8181/mcp` in your `.env`
file so Odysseus picks it up automatically without manual UI config.

## Source

- Package: [`@modelcontextprotocol/server-fetch`](https://www.npmjs.com/package/@modelcontextprotocol/server-fetch)
- Repository: [github.com/modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers/tree/main/src/fetch)
- License: MIT
