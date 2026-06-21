# Bridge Setup — docker.local MCP Wiring

Connects Odysseus to the BeatriceRouter MCP stack running on docker.local.

## Prerequisites
- BeatriceRouter stack deployed and healthy (`curl http://docker.local:10040/health`)
- Odysseus running on the same Tailscale/LAN as docker.local
- Tokens from BeatriceVault (or `.env` on docker.local)

## Steps

1. Copy the template:
   ```
   cp .env.local.example .env.local
   ```

2. Fill in tokens from BeatriceVault:
   - `ROUTER_SERVICE_TOKEN` — issued for `beatrice-model-router-mcp`
   - `RESEARCH_MCP_TOKEN` — issued for `beatrice-research-mcp`
   - `SB_MCP_TOKEN` — issued for `mcps/sb`
   - `SHELL_MCP_TOKEN` — issued for `mcps/shell`
   - `WORKSPACE_MCP_TOKEN` — issued for `mcps/workspace`
   - `SANDBOX_MCP_TOKEN` — issued for `mcps/sandbox`

3. Merge `.env.local` into `.env` before starting Odysseus (or use a shell
   wrapper that exports both files):
   ```
   export $(cat .env .env.local | grep -v '^#' | xargs)
   python app.py
   ```

4. Verify in Odysseus Settings → MCP Servers that all six show as connected.

## Reconnect behavior
The built-in HTTP MCP reconnect loop (added in `feat/http-mcp-builtin-headers-reconnect`)
retries automatically if docker.local is temporarily unreachable. No action needed
on Odysseus restart; MCPs will re-register when docker.local comes back.

## Adding new BeatriceRouter MCP servers
Add a new `ODYSSEUS_MCP_<NAME>_URL` + `ODYSSEUS_MCP_<NAME>_HEADERS` pair to
`.env.local` and restart Odysseus. No code changes required.
