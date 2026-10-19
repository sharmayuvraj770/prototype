# Northstar MCP — Architecture at a glance

> These diagrams show the **target** architecture across all milestones. The README marks
> what exists today (M0+M1: data, init job, mock Public API with its own RBAC).

Broker ops teams already use their own AI assistant (Claude, ChatGPT, Copilot, …).
Northstar ships an **MCP server** that those assistants connect to. The server sits on top of
the existing Public API. Every permission check, approval and audit record lives on the
Northstar side, so every assistant behaves the same way.

## 1. System context

```mermaid
flowchart LR
    subgraph Broker["Broker's world"]
        U[Ops user]
        AI["Their AI assistant<br/>(Claude / ChatGPT / Copilot)"]
    end
    subgraph Northstar["Northstar (we host)"]
        IDP["Northstar IdP<br/>OAuth 2.1 login + consent"]
        MCP["MCP server<br/>identity · tool registry · policy · adapter"]
        UI["Approval page + admin console"]
        API["Public API (existing)<br/>its own RBAC = final authority"]
        DB[(Postgres)]
        R[(Redis cache / rate limits)]
        INT["40+ integrations<br/>ELD · load boards · compliance · EDI"]
    end
    U -- chats --> AI
    AI -- "MCP over HTTPS + bearer token" --> MCP
    AI -. "browser login (once)" .-> IDP
    U -. "approves bookings" .-> UI
    MCP -- "user-scoped token" --> API
    MCP --- R
    UI --> MCP
    API --- DB
    INT -- "already feeds" --> API
```

The assistant never talks to the API or the integrations directly. It only sees MCP tools.

## 2. Connecting an assistant (one time per user)

```mermaid
sequenceDiagram
    autonumber
    actor U as Ops user
    participant AI as User's AI assistant
    participant MCP as Northstar MCP server
    participant IDP as Northstar IdP
    U->>AI: Add connector: https://mcp.northstar.example/mcp
    AI->>MCP: MCP request (no token)
    MCP-->>AI: 401 + WWW-Authenticate → protected-resource metadata
    AI->>MCP: GET /.well-known/oauth-protected-resource
    MCP-->>AI: authorization server = Northstar IdP
    AI->>IDP: Register client (CIMD, or DCR as fallback)
    AI->>U: Opens browser: Northstar login
    U->>IDP: Logs in with Northstar account (SSO)
    IDP->>IDP: Is this AI client allowlisted for the user's broker?
    IDP->>U: Consent: "Claude wants to read loads, quotes…"
    U->>IDP: Approve
    IDP-->>AI: Access token (PKCE) — aud=MCP, sub=user, tenant, role, client_id
    Note over AI,MCP: tenant + role come from Northstar's user record,<br/>signed into the token. The model can't change them.
    AI->>MCP: MCP requests with Bearer token
```

## 3. A read question ("Where's load 4521?")

```mermaid
sequenceDiagram
    autonumber
    participant AI as User's AI
    participant MCP as MCP server
    participant API as Public API
    participant DB as Postgres
    AI->>MCP: tools/call track_load {load_id: 4521}
    MCP->>MCP: L3 verify token → user a.ops, tenant broker_a, role ops_rep
    MCP->>MCP: L4/L5 tool allowed for role? schema valid? kill switch off? rate limit ok?
    MCP->>MCP: cache lookup (key includes tenant + role scope)
    MCP->>API: GET /v1/loads/4521 + /events (token exchanged for API audience, same user)
    API->>API: verify token, RBAC (loads:read, scope = own loads)
    API->>DB: SELECT … WHERE tenant_id = 'broker_a' AND assigned_rep = 'a.ops'
    DB-->>API: row
    API-->>MCP: load + ELD pings (source: eld:samsara-mock)
    MCP->>MCP: compact, redact, untrusted text → untrusted_notes[], confidence, as_of
    MCP-->>AI: structured result + trace_id
    Note over MCP: audit row + JSON log events, all with trace_id
```

## 4. A booking ("Book load 7788 with carrier 55") — tier 2

```mermaid
sequenceDiagram
    autonumber
    actor U as Ops user
    participant AI as User's AI
    participant MCP as MCP server
    participant UI as Northstar approval page
    participant API as Public API
    AI->>MCP: book_load {load_id: 7788, carrier_dot: 55}
    MCP->>MCP: role may book? tenant limit ≤ $5,000? rate resolved server-side (never from the model)
    MCP->>MCP: approval record: user, client, args hash, 5-min expiry, idempotency key
    MCP-->>AI: pending_approval + approval URL
    AI-->>U: "Approve here: https://northstar…/approve/…"
    U->>UI: opens link, re-authenticates (step-up)
    UI->>UI: same user? not expired? args hash matches? load still available? rate unchanged?
    U->>UI: Approve
    UI->>API: POST /v1/loads/7788/book (Idempotency-Key)
    API->>API: RBAC loads:book, tenant scope, carrier eligible
    API-->>UI: booked
    UI-->>U: Booked ✓ (audit: approver, step-up, preview hash)
    Note over AI: The agent never holds a "commit" capability.
```

## 5. How cross-broker access is prevented (defence in depth)

```mermaid
flowchart TB
    Q["Request from broker_a user<br/>asks for broker_b's load 5150"] --> L1
    L1["1 · Signed token<br/>tenant comes from IdP, not the model.<br/>No tool takes a tenant argument (unknown fields rejected)"] --> L2
    L2["2 · MCP policy<br/>role → tool check on every call;<br/>cache key includes tenant + scope"] --> L3
    L3["3 · Public API RBAC<br/>called with the user's own token;<br/>SQL filters WHERE tenant_id = token.tenant"] --> L4
    L4["4 · Same 404 for 'not found' and 'not yours'<br/>no ID enumeration"] --> OUT["Agent sees LOAD_NOT_FOUND_OR_NO_ACCESS"]
    L3 -. "MCP allowed but API denied" .-> AL["Alert: policy-layer bug"]
    OUT --> AUD["Audit row + trace"]
```

Even if layer 2 had a bug, layer 3 still refuses, because the API trusts only the signed
token and never anything in the MCP request body. The demo shows this on purpose.

## 6. Deployment (local, one command)

```mermaid
flowchart LR
    PG[(postgres)] -->|healthy| INIT["init (one-shot)<br/>migrate · load seed CSVs · generate · keys"]
    RD[(redis)]
    INIT -->|completed| API[mock_tms_api :8001]
    INIT -->|completed| IDP[mock_idp :9000]
    API -->|healthy| MCP[mcp_server :8000]
    IDP -->|healthy| MCP
    RD --> MCP
```

`docker compose up --build`. Reset with `docker compose down -v`. Optional profiles:
`tracing` (Jaeger), `refresh-data` (re-download public data).

## 7. Generated tools from the OpenAPI spec

```mermaid
flowchart LR
    SPEC["Public API /openapi.json"] --> BR["MCP OpenAPI bridge"]
    MAN["mcp_manifest.yaml<br/>allowlisted operationIds,<br/>tier, roles, description"] --> BR
    BR -->|"listed + schema hash matches"| T["Tool registered<br/>(same policy layer)"]
    BR -->|"missing / spec drifted"| X["Tool disabled + logged"]
    BR -->|"not in manifest"| N["Never exposed"]
```
