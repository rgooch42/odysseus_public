# BeatriceRouter → Odysseus Integration Design

**Date:** 2026-06-21
**Status:** Approved
**Applies to:** odysseus_local (private fork) + Odysseus_Public (upstream contribution plan)

---

## 1. Problem Statement

BeatriceRouter provides strong local infrastructure (job queue, harness runner,
capability routing, sandbox pool, reflection pipeline, vault secrets) that
Odysseus lacks. Odysseus provides a stronger security model, richer tool
surface, and a maintained community codebase that BeatriceRouter lacks.
The goal is to absorb BeatriceRouter's orchestration strengths into Odysseus
without duplicating effort, while keeping proprietary workflows private.

---

## 2. Strategy

**Bridge now, plugin system as the long-term anchor.**

- **Phase A (immediate):** Wire BeatriceRouter's existing MCP servers into
  Odysseus via `ODYSSEUS_MCP_*` HTTP built-in server env vars. No code changes —
  config only. Tracked in `rgooch42/odysseus_local` (private).

- **Phase B (contribution):** Design and contribute a plugin/module architecture
  to upstream Odysseus. Contribute generalized orchestration features
  (job queue, harness, sandbox interface, vault integration, remote execution
  host, capability routing). Keep private: our specific project types, tool
  contracts, arbiter, reflection data, infra config.

- **Phase C (private modules):** Once the plugin system exists upstream, migrate
  private BeatriceRouter implementations into Odysseus plugin modules stored in
  `odysseus_local` (or a separate private modules repo).

---

## 3. Repository Model

| Repo | Visibility | Purpose |
|------|-----------|---------|
| `rgooch42/odysseus_local` | Private | Personal fork; bridge config, private overlay, local patches |
| `rgooch42/odysseus_public` | Public | Staging fork; Phase B contributions visible before PRing upstream |
| `pewdiepie-archdaemon/odysseus` | Public | Upstream; final destination for community contributions |
| *(future)* `rgooch42/beatrice-modules` | Private | Private plugin implementations (Phase C) |

**Remote convention in `odysseus_local`:**
- `origin`   → `rgooch42/odysseus_local` (private)
- `public`   → `rgooch42/odysseus_public` (public staging)
- `upstream` → `pewdiepie-archdaemon/odysseus` (upstream)

**Contribution flow:**
```
odysseus_local feat/* → push to public → PR to upstream
```
Private config never touches `public` or `upstream`.

**Branch convention:**
- `feat/*` — intended for upstream PR; no private config
- `local/*` — private-only; never pushed to upstream fork

---

## 4. Phase A: Bridge (Complete)

Six BeatriceRouter MCP servers registered as Odysseus HTTP built-in MCPs via
`.env.local` (gitignored). Template at `.env.local.example`.

| MCP | Endpoint | Purpose |
|-----|----------|---------|
| `MODEL_ROUTER` | `docker.local:10040` | Capability-aware model dispatch |
| `RESEARCH` | `docker.local:10020` | SearXNG-backed web research |
| `SB` | `docker.local:10010` | SecondBrain vault (notes, tasks, handoffs) |
| `SHELL` | `docker.local:10030` | Remote shell execution on docker.local |
| `WORKSPACE` | `docker.local:10031` | Remote file workspace |
| `SANDBOX` | `docker.local:10050` | Container acquire/release for agent tasks |

Reconnect loop handles docker.local going offline. Auth via static bearer
tokens in `ODYSSEUS_MCP_*_HEADERS`.

Setup guide: `local/docs/bridge-setup.md`

---

## 5. Phase B: Upstream Contributions

### 5.1 Plugin / Module Architecture

The foundational contribution. Odysseus has no extension system today.
Discovery mirrors the existing integration autodiscovery pattern: plugins are
found automatically in a `plugins/` directory; each gets an on/off toggle in
Settings.

**Plugin manifest (YAML):**
```yaml
name: my-plugin
version: 1.0.0
routes: routes/my_plugin_routes.py        # optional
tools: src/tools/my_plugin_tools.py       # optional
mcp_servers:                              # optional
  - name: my-mcp
    url_env: MY_MCP_URL
    headers_env: MY_MCP_HEADERS
ui_panels:                                # optional
  - id: my-panel
    label: My Panel
    icon: icon-name
    template: templates/my_panel.html
db_models: core/my_plugin_models.py       # optional; migrated at startup
settings_namespace: my_plugin             # optional prefs prefix
```

**Plugin loader:**
- Discovers plugins from a configurable `plugins/` directory at startup
- Validates manifests, registers routes under `/plugins/<name>/`
- Registers tool schemas + implementations into the tool registry
- Creates DB tables for plugin models
- Injects UI panels into the sidebar/settings

### 5.2 Job Queue System

Generalized from BeatriceRouter's Redis/RQ implementation. Extends (never
replaces) Odysseus's existing `task_scheduler.py` — existing scheduled task
behavior is fully preserved.

- Redis/RQ-based async job executor with configurable retry, TTL, and priority
- Queue dashboard: pending / running / history / failed views
- Job detail: logs, tool calls made, output, token usage, duration
- SSE push for live queue status updates
- `task_scheduler.py` gains a Redis backend option; falls back to existing
  in-process scheduler when Redis is unavailable

### 5.3 Harness Runner (Evaluation Framework)

Model evaluation runner, contributed as a **plugin** (not core — keeps the
base install lean; users opt in).

- TOML-defined harness configs: model, tool set, round limit, context budget,
  assertions (expected tool calls, output patterns, pass/fail criteria)
- Runner executes a config against any configured Odysseus provider
- Result capture: tool calls made, tokens used, pass/fail per assertion, latency
- Harness library UI: browse configs, run, view results, compare runs
- **Private:** our specific harness TOML files and project-type contracts

### 5.4 Reflection / Feedback Pipeline (Framework)

Hook points only — implementation and dataset stay private.

- Review queue: agent outputs flagged for human review (by tool, by score threshold)
- Audit interface: approve / reject / annotate outputs
- JSONL export: canonical schema with train/eval disjoint split and eval hash
- Abstract hook: `on_reflection_export(dataset_path)` for downstream pipelines
- **Private:** our dataset builder, fine-tuning pipeline, specific annotations

### 5.5 Sandbox Pool Interface

Abstract contract for container-backed agent execution.

- `acquire(profile: str) → container_url` / `release(container_url)` interface
- Pool status dashboard: available / leased / queue depth per profile
- Health probe loop with autoheal
- **Private:** our specific container profiles, docker.local pool implementation

### 5.6 Capability Contracts

Per-project-type routing and tool scoping.

- Project type registry: named types with required tools, preferred model
  capability tier, optional container profile
- Per-type tool allowlist (layered on top of Odysseus's existing `tool_security.py`)
- Contract validation on job submission (fails fast if required tools unavailable)
- Capability evaluation: probe a model before routing to confirm it meets tier
- **Private:** our specific project type definitions

### 5.7 OpenBao / Vault-Compatible Secrets Integration

Replaces `.env` token passing with a centralized secrets provider.

- Generic Vault HTTP API client (works with OpenBao, HashiCorp Vault, HCP Vault)
- AppRole auth: `VAULT_ADDR`, `VAULT_ROLE_ID`, `VAULT_SECRET_ID` env vars
- Secret fetch at startup; Odysseus resolves `vault:path/to/secret` references
  in config values
- Rotation-aware: re-fetch on TTL expiry without restart
- **Private:** our vault address, role IDs, secret IDs, path structure

### 5.8 Remote Docker Execution Host

Generic "add a remote machine to run tools on" capability.

- Register a remote host (hostname, auth method, available tool profiles)
- Tool calls for `bash`, `python`, file ops can be dispatched to the remote host
  instead of localhost
- Health probe; fallback to local if remote unreachable
- UI: registered hosts, status, active sessions
- **Private:** our docker.local host config

### 5.9 Remote Inference Offloading

Extends Odysseus's local-first model approach to self-hosted remote inference.

- Register a remote vLLM / Ollama / OpenAI-compatible endpoint as a backend
- Capability tier tagging: label endpoints as `general`, `coder`, `embedding`, etc.
- Offload routing: jobs with a capability requirement route to a matching remote
  backend if local models don't satisfy it
- Dashboard: registered backends, health, active load
- **Private:** our DGX Spark endpoint, model stack, routing config

---

## 6. Private Modules (Phase C)

These implement the Phase B interfaces against our specific infrastructure.
None of this goes upstream.

| Module | Implements | Contents |
|--------|-----------|---------|
| `beatrice-infra` | Remote execution host | docker.local connector, container lifecycle |
| `beatrice-routing` | Remote inference offloading | DGX Spark / vLLM config, coder-next lease |
| `beatrice-project-types` | Capability contracts | Our project type definitions + tool contracts |
| `beatrice-sandbox` | Sandbox pool | Our container profiles and pool sizing |
| `beatrice-arbiter` | (new hook TBD) | Approval/notification agent, VS Code, ntfy |
| `beatrice-reflection` | Reflection pipeline | Our dataset builder, fine-tuning pipeline |
| `beatrice-vault` | Vault integration | Our OpenBao config, paths, role IDs |

---

## 7. Privacy Boundary Summary

**Safe to share — reveals no secret sauce:**
- Plugin architecture (the framework itself)
- Job queue system (Redis/RQ, generalized)
- Harness runner (evaluation framework and UI)
- Reflection pipeline (hooks and audit UI — not our data)
- Sandbox pool interface (abstract contract)
- Capability contracts (schema and routing logic — not our type definitions)
- OpenBao / Vault-compatible secrets integration
- Remote docker execution host (generic)
- Remote inference offloading (framework)

**Must stay private:**
- Our project type definitions and tool contracts
- Our arbiter connector and approval workflows
- Our reflection dataset and fine-tuning pipeline
- Our BeatriceRouter / DGX Spark / Unifi endpoint config
- Our specific sandbox pool profiles
- Our vault address, role IDs, and secret paths

---

## 8. Decisions

1. **Plugin discovery:** Mirror Odysseus's existing autodiscovery pattern.
   Plugins are discovered automatically from a `plugins/` directory; each plugin
   gets an on/off toggle in Settings (config slider), matching how Odysseus
   handles existing integrations. No explicit list in `settings.py`.

2. **Job queue vs. `task_scheduler.py`:** Extend, never replace. The job queue
   system wraps and builds on top of `task_scheduler.py` — adding Redis/RQ-backed
   async execution, queue dashboard, and SSE updates as an additive layer.
   Existing scheduled task behavior is preserved.

3. **Harness runner:** Plugin, not core. Keeps the base install lean; users who
   want model evaluation opt in.

4. **Unifi integration:** Revisit as a community plugin once the plugin system
   exists. Contributes DNS/network awareness without revealing our specific
   infrastructure config.

5. **`Odysseus_Public` repo:** `rgooch42/odysseus_public` created as a public
   fork of `pewdiepie-archdaemon/odysseus`. Phase B contributions stage here
   before PRing upstream. Added as remote `public` in `odysseus_local`.
