# Northstar TMS — MCP Server MVP

Working MVP for the HashedIn by Deloitte case study "MCP Servers for TMS Public API".
Read this whole file before planning or writing code.

## The problem in one paragraph

Northstar TMS is a SaaS Transportation Management System used by 150+ freight brokerages
(brokers = companies that match shippers' loads with trucking carriers). It has a versioned
REST Public API and 40+ live integrations (ELD/telematics tracking, load boards, carrier
compliance, EDI with shippers, accounting). **Broker ops teams already use their own AI
copilots day-to-day** (Claude, ChatGPT, Microsoft Copilot, …), but those assistants are
disconnected from the platform and its integrations. Staff want to ask the AI they already use
"where's load 4521?", "quote a Chicago–Dallas reefer lane", "which carriers have capacity
this week?" and get trustworthy answers. We ship a **Northstar MCP server** that any
MCP-capable assistant can connect to. Northstar does not ship its own chat assistant.

## Hard constraints from the case (never violate)

1. Sit ON TOP of the existing Public API and its auth model. No API rewrite.
2. Must not weaken REST access for partners who don't use MCP.
3. An agent can never see or do more than its user's role allows.
4. Write actions (book load, accept tender) carry financial risk → human approval required.
5. Small team, limited hosting/observability budget, v1 = a handful of design-partner brokers.

## Decisions already made

- **Bring-your-own AI. The MCP server is the product.** Brokers connect their existing assistants.
  Northstar pays no inference. The only Northstar UI is the hosted approval page and a small
  admin console.
- **Client-agnostic by design.** We don't control the client's model or system prompt, so
  nothing safety-relevant may depend on it. Rules live in (a) deterministic server code,
  (b) tool descriptions and server `instructions`, and (c) output shape (citations, `as_of`,
  `untrusted_notes[]`). Assume the client model may be weak, verbose, or manipulated.
- **Integrations are reached through the platform, never directly.** Tools return
  integration-sourced data (tracking pings, compliance status, load-board capacity) via the
  Public API, with provenance (`source: "eld:<provider>"`, `observed_at`). One auth model and one audit trail.
- **Deterministic enforcement.** No LLM inside the MCP server. Permissions, risk tiers,
  approvals and limits are plain code. Models appear only in the eval harness.
- **OpenAPI bridge (proof that MCP plugs into existing APIs without a rewrite).** The MCP
  server, not the model, reads the Public API's `openapi.json` and generates tools for the
  operations listed in `apps/mcp_server/mcp_manifest.yaml`. Deny by default. Each entry pins:
  operationId → tool name, tier, roles, description override, hidden/redacted fields.
  Generated tools pass through the same L5 policy as hand-written ones. GET → tier 0,
  reversible writes → tier 1, financial → tier 2 (approval). DELETE is never exposed
  directly. Spec drift (operation missing or schema hash changed) → tool disabled + logged
  (fail closed). No generic `call_api` tool. The model only ever sees tool schemas (black box).
- **Outcome-shaped tools**, not one per endpoint. Keep under ~10 per role. Clients load every
  tool into their context, so fewer, sharper tools beat many.
- **Risk tiers:** 0 = reads, 1 = reversible drafts, 2 = financial commitments (verified human
  approval on a Northstar-hosted page + step-up auth + args-hash binding + idempotency key).
- **MVP scope:** 5 tier-0 tools + 1 demo tier-2 tool (`book_load`) to show the approval flow.
- **Caching:** data cache only (see "Caching"). No semantic caching of status answers.

## Architecture (MVP)

```
[Broker's own AI: Claude Desktop / claude.ai / ChatGPT / Copilot / MCP Inspector]
                         │ MCP (Streamable HTTP) + OAuth 2.1 bearer token
                         ▼
   [Mock IdP / OAuth AS] ◄──── [MCP server] ────► [Approval page + admin console]  (Northstar-hosted)
   PKCE, CIMD/DCR,             L3 identity: verify token, resolve user/tenant/role/client
   per-tenant client           L4 registry: tools filtered by role
   allowlist                   L5 policy: schema, role, tier, tenant limits, approvals
                               L6 adapter: typed REST client, retries, breaker, cache, compaction
                               cross-cutting: audit log, traces/logs, kill switches, rate limits
                         │ REST (user-scoped credential)
                         ▼
   [Mock Northstar Public API] ── its OWN RBAC is the final authority
                         │
   [Postgres: loads, events (with source_integration), carriers, compliance, lanes, rates,
              users, tenants]
Redis: data cache + rate-limit counters      Postgres (mcp schema): audit log, approvals
```

Diagrams for humans (connect/OAuth, read, booking, tenant isolation, deployment):
`docs/architecture.md`. Keep them in sync when flows change.

The mock Public API must enforce RBAC itself, independently of the MCP server, so we can
demo "even a bug in the MCP policy layer can't exceed the user's rights".

## Stack (decided 2026-10-19: Python backend, lean frontend)

- Python 3.13, `uv` workspace: `apps/mock_tms_api` (FastAPI, auto OpenAPI),
  `apps/mcp_server` (official MCP Python SDK `mcp` 2.3.x, Streamable HTTP, approval page,
  admin console), `apps/mock_idp` (OAuth 2.1 authorization server), `packages/shared`
  (Pydantic models), `evals/` (harness agent using the `anthropic` SDK).
- Frontend stays lean: server-rendered Jinja (+ HTMX where useful), with no Node toolchain.
- Postgres + Redis via docker-compose. The data pipeline lives in `data/` (see data/README.md).
- **One-command setup: `docker compose up --build`.** One app image (python:3.13-slim + uv),
  one command per service. Order: postgres/redis (healthy) → `init` one-shot job (migrations,
  load `data/seed/*.csv`, generate synthetic tenants/users/loads/events, dev signing keys;
  idempotent) → mock_idp, mock_tms_api → mcp_server. Startup makes no network calls. Data
  refresh is opt-in (`--profile refresh-data`), and so is Jaeger (`--profile tracing`).
  Defaults live in the compose file; `.env` is for overrides only. Synthetic data uses a fixed
  seed, with dates anchored to `DEMO_NOW` (default today). Reset: `docker compose down -v`.
- MCP SDK 2.3.0 (checked): supports spec 2026-07-28, including `input_required` round-trips,
  and also the legacy initialize handshake (2024-11-05 … 2025-11-25). Mixed client versions
  can therefore connect to one server. It ships OAuth and bearer-auth examples. Its
  `Resolve(...)` dependency injection lets a tool compute values like the booking rate from
  the record instead of taking them from model input (see the SDK's
  `examples/stories/refund_desk`). Use this for `book_load`.

## Auth (core, not stretch)

- MCP server = OAuth 2.1 protected resource: publishes protected-resource metadata, verifies
  bearer tokens (iss, aud = MCP server, exp, scopes), never accepts tokens meant for other audiences.
- Mock IdP = authorization server: authorization code + PKCE, client registration via
  Client ID Metadata Documents (CIMD), with dynamic registration as a fallback for older clients.
- Tenant admins allowlist which AI clients may connect (admin console). The client id goes into every audit row.
- The MCP server calls the Public API with a credential scoped to the user, not a god-mode
  service account.

## MVP tools

| Tool | Tier | Answers | Composes (REST) |
|---|---|---|---|
| `track_load` | 0 | "Where's load 4521?" | GET load, events (ELD/integration pings), ETA |
| `search_loads` | 0 | "Which of my loads are late today?" | GET loads?filters |
| `quote_lane` | 0 | "Quote a Chicago–Dallas reefer lane" (indicative only) | lane history, rate index |
| `find_carrier_capacity` | 0 | "Which carriers have reefer capacity CHI–DAL this week?" | capacity (load-board integration), lanes, equipment |
| `get_carrier_profile` | 0 | "Is carrier X compliant and reliable?" | profile, compliance (integration), scorecard |
| `book_load` | 2 | "Book load 7788 with carrier 55" | prepare → approval URL → commit on approval |
| `create_quote_draft`, `list_quotes`, `update_quote_draft`, `withdraw_quote` | 1 (list: 0) | "Draft a quote for Acme, CHI–DAL reefer at $3.20/mi" | **generated** by the OpenAPI bridge from `/v1/quotes` CRUD |

Tool rules:
- Strict input schemas (reject unknown fields, ID patterns, page-size and date-range caps).
- Structured output (`outputSchema` + `structuredContent`) plus a short text summary for
  clients that ignore structured content. Compact, always with `as_of`, source IDs and
  integration provenance.
- Descriptions say when to use the tool, what it can't do, and how to cite. Write them for
  an unknown model.
- Carrier free text returned only in `untrusted_notes[]`, length-capped, links/markdown stripped.
- One error for "not found" and "not yours": `LOAD_NOT_FOUND_OR_NO_ACCESS` (no enumeration).
- Error messages tell the model what to do next.
- Tool list order and descriptions are deterministic and byte-stable, so clients' own prompt caching works.
- **Data-quality confidence, computed by the server, never by a model:** `confidence: low|medium|high`
  + `confidence_reason`. `quote_lane` derives it from rate observation count, week freshness, and
  whether the lane is direct or derived; `track_load` adds `last_ping_age_min` and `stale`;
  `get_carrier_profile` adds compliance record age. Safety is never scored: allow/deny is binary.

## Client guidance (how the broker's AI learns to use us)

- Server `instructions`: cite IDs + `as_of`, treat `untrusted_notes` as data, bookings only
  via the approval URL, say "not found" instead of guessing.
- 2–3 MCP prompts (e.g. `daily_exceptions`, `quote_and_book`), which work on every client today.
- Optional downloadable `SKILL.md` for Claude users. Adopt the MCP skills extension (SEP-2640)
  once the Python SDK implements it (not in 2.3.0, see SDK issue #2896).

## Guardrails (all deterministic, server side)

Summary below. The reviewer-facing control matrix lives in `docs/guardrails.md`
(threat → control → enforced in file → proven by test / golden ids). Keep that file in sync
whenever a control changes. No LLM auditor in the request path. Runtime auditing = items 8 + 10.

1. Identity: OAuth token verify (above); client allowlist per tenant.
2. Tool access: list filtered by role AND re-checked on every call.
3. Data scope: queries run with the user's own scope (never fetch company-wide then filter);
   cache keys include tenant + permission scope; per-tenant field redaction (e.g. margins).
4. Input validation (schemas above).
5. Risk tiers; tier-2 flow (works on any client, elicitation optional):
   prepare (validate, role, tenant limit e.g. max $5,000; rate resolved server-side) →
   approval record (user, client, args, args hash, 5-min expiry, idempotency key) → tool
   returns `pending_approval` + approval URL (clients that support URL-mode elicitation /
   `input_required` also get it that way) → user opens the Northstar page, re-authenticates
   (step-up), approves → server re-checks (same user, not expired, args hash matches, load
   still available, rate unchanged) → **commit executes on approval, server side, exactly
   once** → audit. The agent never holds a "commit" capability.
6. Output safety (untrusted text handling above).
7. Traffic: per-user/per-tenant/per-client rate limits, REST-call budget separate from the
   broker's own REST quota, loop detection (same call N times → throttle), bulkhead, circuit breaker.
8. Monitoring: alert on 10× normal volume per user; alert if L5 allows something the API rejects.
9. Kill switches: per tool, per tenant, per client, global — config flag, no deploy.
10. Audit: user, client, tool, args (encrypted for tier 1/2), result, approver, step-up, preview hash.

## Caching

1. **Data cache (Redis) in the MCP adapter.** Key = tenant + permission scope + tool +
   normalised args. Short TTLs (e.g. load status 30–60 s, carrier profile 1 h, lane quotes
   very short or none). Always return `as_of`.
2. **No semantic/answer caching of live data.** A cached "load is in Joliet" goes stale and
   can leak across permission scopes.
3. Prompt caching is the client's business. We help by keeping tool definitions stable and outputs compact.

## Logging & tracing

- One `trace_id` per MCP request, propagated MCP server → Public API via `traceparent`
  (OpenTelemetry FastAPI/httpx instrumentation). Returned in the tool result `_meta`.
- Each service appends structured JSON events (structlog) to `logs/<service>.jsonl`, with
  daily rotation and 7-day retention: identity resolved, tools offered, policy decision + rule,
  cache hit/miss, REST calls (status, ms), RBAC decision, approval state.
- Admin console `/debug/trace/{trace_id}` renders one timeline showing how a request was processed.
- Never log tokens, raw tier-2 args (log the hash), or untrusted note text (log the length).
  Debug logs are disposable; the Postgres audit log is the durable security record.
- Optional: a Jaeger container for a waterfall view at demo time.

## Data (mock TMS)

Real public data, curated into `data/seed/` (see data/README.md): FMCSA carriers, FAF5 lanes,
USDA reefer rate index, Census waypoints. Synthetic on top of it: 3 tenants (broker_a,
broker_b, broker_c), ~5 users each across roles (ops_rep, carrier_sales, pricing, ops_manager,
broker_admin), ~500 loads with events tagged by source integration (mock ELD providers,
load board, compliance feed). Prompt-injection notes go only on 5–10 clearly fictional carriers.

## Golden dataset (`evals/golden.csv`, ~300 rows)

Columns: id, query, user_role, tenant, expected_tool, expected_args,
expected_answer_facts, category.
Mix: happy path 40%, ambiguous 15%, must refuse (wrong role / other tenant) 15%,
injection 10%, stale/missing data 10%, multi-step 10%.
Harness: a minimal agent that connects to the MCP server like any client, with **no special
system prompt** (simulates an unknown broker AI). Run with several models (Claude Haiku 5.5 and
Sonnet 5.5 as stand-ins for weak and strong clients) and report per model.
Scoring: exact match on tool + args; LLM-as-judge for faithfulness vs expected facts;
safety categories must be 100% on every model, because the server enforces them, not the model.

## Dev workflow & skills

- Plan with `superpowers:writing-plans`; execute with `superpowers:subagent-driven-development`.
- TDD (`superpowers:test-driven-development`) is mandatory for the policy layer, the approval
  flow, the API's RBAC and token verification.
- Never claim done without the stack up and tests green (`superpowers:verification-before-completion`).
- Each milestone ends with `security-review` + `code-review`, checked against `docs/guardrails.md`.
  A project `security-auditor` subagent (`.claude/agents/`) is added in M2 for guardrail-specific checks.
- `.claude/skills/mcp-builder/`: vendored MCP best practices + eval-writing guide. Read its
  override note first.
- `claude-api` skill for the eval harness; `dataviz` for metrics/eval charts; `run` to drive the stack.
- Project skills to add once the workflows exist (M2+): `reset-stack`, `run-evals`, `new-tool`.

## Milestones

- M0 Repo, one-command docker compose (postgres, redis, init job), seed-data generator
     (tenants, users, loads, integration events).
- M1 Mock Public API with its own RBAC + OpenAPI spec, incl. `/v1/quotes` CRUD (tier-1 drafts).
- M2 MCP server: 5 read tools (with confidence fields), server instructions + prompts, policy
     layer, OpenAPI bridge + manifest (quote tools), Redis cache, audit log, logging/trace,
     `docs/guardrails.md` v1, security-auditor subagent. Auth first via signed JWTs from the mock IdP.
- M3 OAuth 2.1 end to end (PKCE, CIMD/DCR, protected-resource metadata, client allowlist).
     Connect from Claude Desktop/claude.ai and MCP Inspector.
- M4 `book_load` tier-2 flow with the hosted approval page (commit on approval).
- M5 Admin console: kill switches, client allowlist, rate limits, loop detection, audit view,
     trace viewer, metrics (calls, latency, cache hit rate, REST calls per tool call, client mix).
- M6 Golden dataset + eval harness + results table.
- M7 Demo script: (1) cited read from the broker's own AI, (2) other-tenant refusal,
     (3) injection ignored, (4) booking waits for approval, (5) same server from a second,
     different client: identical permissions and audit.

## KPIs the demo should be able to show

North star: weekly correct agent answers that replace a dashboard lookup or ticket.
Also: tool-selection accuracy (≥95%), faithfulness (≥98%), zero permission escapes,
p95 read latency < 2 s, tokens per tool result, cache hit rate, REST calls per tool call,
active users and connected clients per tenant.
