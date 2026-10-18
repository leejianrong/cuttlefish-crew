// The C4 model of cuttlefish-crew: five views, one source. `node render.mjs` (or `make diagrams`)
// draws each to an SVG in docs-site/assets/architecture/. Edit this, not the SVGs.
//
// A node is { id, kind, name, tech?, desc?, children? }. Kinds follow C4: person, system, ext
// (external system), container, db, ext-container, ext-db, component, boundary (a dashed group
// that holds children), deploy (a deployment node, drawn as a labelled box that holds children).
// An edge is [from, to, "what it does", "how"]. `dir` is the way the layout reads.
//
// Where a view disagrees with the code, the code is right: fix the view in the same PR.

export const views = [
  {
    id: "c4-1-context",
    title: "1. System context",
    dir: "RIGHT",
    nodes: [
      { id: "operator", kind: "person", name: "Operator", desc: "Owns the PC and the projects. Watches teams, answers Needs you, steers." },
      { id: "cuttlefish", kind: "system", name: "cuttlefish-crew", desc: "Fleet manager for small teams of coding sub-agents across many projects, with a dashboard." },
      { id: "agents", kind: "ext", name: "Coding agent CLIs", desc: "kopicode, Claude Code and Codex. Each does the coding through its own headless surface." },
      { id: "llm", kind: "ext", name: "Summarising model", desc: "OpenRouter or Anthropic. Writes handover summaries only, never the coding." },
      { id: "sandbox", kind: "ext", name: "Sandbox provider", desc: "Docker or E2B. Optional containment for an agent's commands." },
      { id: "tailscale", kind: "ext", name: "Tailscale", desc: "Private network and https certificates between the PC and the phone." },
    ],
    edges: [
      ["operator", "cuttlefish", "Starts teams, answers requests, reads the journal", "dashboard, CLI, MCP"],
      ["cuttlefish", "agents", "Delegates a round of work to", "stdio, JSON streams"],
      ["cuttlefish", "llm", "Asks for a handover summary", "HTTPS API"],
      ["cuttlefish", "sandbox", "Runs an agent inside, when set", "Docker, E2B API"],
      ["operator", "tailscale", "Reaches the PC from a phone over", "WireGuard"],
      ["tailscale", "cuttlefish", "Forwards https and wss to", "tailscale serve"],
    ],
    note: "Not shown: satay-runtime (a library inside the daemon, not a system) and the model providers the coding agents call themselves.",
  },

  {
    id: "c4-2-containers",
    title: "2. Containers",
    dir: "DOWN",
    nodes: [
      { id: "operator", kind: "person", name: "Operator", desc: "On the PC, or a paired phone" },
      {
        id: "cc", kind: "boundary", name: "cuttlefish-crew", tech: "System",
        children: [
          { id: "dashboard", kind: "container", name: "Dashboard", tech: "Svelte, TypeScript, Vite", desc: "Sprites, Needs you, Terminal, Devices. Served by the daemon, same origin." },
          { id: "cli", kind: "container", name: "CLI", tech: "Python", desc: "run, run-team, serve, relay, init, doctor, secrets. One-shot runs happen in this process." },
          { id: "mcp", kind: "container", name: "MCP server", tech: "Python, stdio", desc: "Lets an agent drive the daemon's API." },
          { id: "daemon", kind: "container", name: "Fleet daemon", tech: "Python, FastAPI, satay", desc: "cuttlefish serve. Runs every project's team as asyncio tasks. Serves the API, the terminal and the relay link." },
          { id: "relay", kind: "container", name: "Relay", tech: "Python, FastAPI", desc: "cuttlefish relay. Pairs a daemon with browsers and copies encrypted bytes. Cannot read them." },
          { id: "session", kind: "container", name: "Agent session", tech: "tmux; claude, codex or kopicode", desc: "One interactive agent per project, on cuttlefish's own tmux socket." },
          { id: "hook", kind: "container", name: "Hook script", tech: "Python, standard library", desc: "Run by Claude Code per hook. Asks the daemon; prints nothing if it cannot decide." },
          {
            id: "disk", kind: "boundary", name: "State on disk", tech: "SQLite files",
            children: [
            { id: "projects", kind: "db", name: "Project registry", tech: "SQLite, ~/.cuttlefish/projects.db", desc: "Each project's identity, roles and settings." },
            { id: "journal", kind: "db", name: "Episodic journal", tech: "SQLite, <root>/.cuttlefish/episodic.db", desc: "Append-only, redacted at write time. Everything shown derives from it." },
            { id: "satay", kind: "db", name: "satay store", tech: "SQLite, <root>/.satay", desc: "Durable workflow state per project, so a restart resumes." },
            { id: "secrets", kind: "db", name: "Secrets store", tech: "SQLite, encrypted, ~/.cuttlefish/secrets.db", desc: "Project and shared secrets. Needs CUTTLEFISH_SECRETS_KEY." },
            { id: "devices", kind: "db", name: "Device store", tech: "SQLite, ~/.cuttlefish/devices.db", desc: "Paired devices, keys and tiers, beside the daemon's relay key." },
            ],
          },
        ],
      },
      { id: "agents", kind: "ext", name: "Coding agent CLIs", desc: "kopicode, Claude Code, Codex" },
      { id: "llm", kind: "ext", name: "Summarising model", desc: "OpenRouter or Anthropic" },
      { id: "sandbox", kind: "ext", name: "Sandbox provider", desc: "Docker or E2B" },
    ],
    edges: [
      ["operator", "dashboard", "Uses", "browser"],
      ["operator", "cli", "Runs", "shell"],
      ["dashboard", "daemon", "Calls", "JSON over HTTP, websocket"],
      ["dashboard", "relay", "Or calls through, from a phone", "wss, Noise-encrypted"],
      ["daemon", "relay", "Dials out to", "ws or wss, Noise-encrypted"],
      ["mcp", "daemon", "Calls", "JSON over HTTP"],
      ["daemon", "agents", "Delegates rounds to", "stdio, JSON streams"],
      ["daemon", "session", "Starts, reattaches, bridges a terminal to", "tmux, pty"],
      ["session", "hook", "Runs on each hook", "process"],
      ["hook", "daemon", "Asks about a permission", "HTTP, hook token"],
      ["daemon", "journal", "Appends to, reads from", ""],
      ["daemon", "satay", "Runs workflows in", ""],
      ["daemon", "projects", "Reads and writes", ""],
      ["daemon", "secrets", "Resolves secrets from", ""],
      ["daemon", "devices", "Checks and updates", ""],
      ["daemon", "llm", "Summarises a handover with", "HTTPS"],
      ["daemon", "sandbox", "Runs an agent in", "Docker, E2B API"],
      ["cli", "agents", "Delegates a one-shot run to", "stdio"],
      ["cli", "journal", "Appends to", ""],
    ],
    note: "cuttlefish.relay.protocol and frontend/src/lib/relay/ import nothing else from cuttlefish, so they can be lifted into their own repository (ADR-0037, enforced by a test).",
  },

  {
    id: "c4-3a-team",
    title: "3a. Fleet daemon: running a team",
    dir: "DOWN",
    nodes: [
      { id: "dashboard", kind: "ext-container", name: "Dashboard", tech: "Svelte", desc: "Starts, stops and steers a team" },
      {
        id: "daemon", kind: "boundary", name: "Fleet daemon", tech: "Container",
        children: [
          { id: "fleet", kind: "component", name: "FleetDaemon", tech: "fleet/daemon.py", desc: "One asyncio task and one satay engine per project. Resumes unfinished teams after a crash." },
          { id: "team", kind: "component", name: "Team and workflow", tech: "team.py, workflow.py", desc: "The satay workflows run_task and run_team. Roles run concurrently under one task id." },
          { id: "memory", kind: "component", name: "Working memory", tech: "handover.py, limits.py, budget.py, llm/", desc: "Token budget check, handover summaries, turn caps and the per-role spend stop." },
          { id: "tasks", kind: "component", name: "Satay tasks", tech: "tasks/", desc: "The three places effects happen: delegate (a side effect), journal append, own LLM call." },
          { id: "seam", kind: "component", name: "AgentBackend seam", tech: "agents/", desc: "Protocol and registry. Each backend turns its tool's output into a DelegationOutcome." },
          { id: "journal", kind: "component", name: "Episodic store", tech: "episodic/", desc: "Tagged-union events, secrets redacted at write time, append-only." },
          { id: "secrets", kind: "component", name: "Secrets and broker", tech: "secrets/, broker.py", desc: "Resolves a secret inside the delegation task. With the broker on, leases a short-lived token instead of the model key." },
          { id: "transports", kind: "component", name: "Backend transports", tech: "delegate/", desc: "kopicode serve or print, Claude Code stream-json or print, Codex app-server or exec. Live ones can pause for an answer." },
          { id: "perms", kind: "component", name: "Permissions and Needs you", tech: "permissions.py, requests.py, delegate/never_allowed.py", desc: "Modes, per-role access and the shared never-allowed list. A request is two journal events." },
          { id: "sandboxc", kind: "component", name: "Sandbox providers", tech: "sandbox/", desc: "Container and E2B providers behind one interface." },
        ],
      },
      { id: "agents", kind: "ext", name: "Coding agent CLIs", desc: "kopicode, Claude Code, Codex" },
      { id: "llm", kind: "ext", name: "Summarising model", desc: "OpenRouter or Anthropic" },
      { id: "journaldb", kind: "ext-db", name: "Episodic journal", tech: "SQLite", desc: "<root>/.cuttlefish/episodic.db" },
      { id: "secretsdb", kind: "ext-db", name: "Secrets store", tech: "SQLite, encrypted", desc: "~/.cuttlefish/secrets.db" },
    ],
    edges: [
      ["dashboard", "fleet", "start, stop, steer, approve", "HTTP"],
      ["fleet", "team", "Starts through satay", "run_app"],
      ["team", "memory", "Checks budget, hands over with", ""],
      ["team", "tasks", "Calls", ""],
      ["tasks", "seam", "Delegates through", ""],
      ["seam", "transports", "Uses the selected", ""],
      ["transports", "agents", "Spawns and talks to", "stdio"],
      ["transports", "perms", "Asks for an answer from", ""],
      ["transports", "sandboxc", "Runs inside, when set", ""],
      ["tasks", "journal", "Appends events through", ""],
      ["tasks", "secrets", "Resolves inside the task; never returns the value", ""],
      ["perms", "journal", "Records requests and answers in", ""],
      ["journal", "journaldb", "Writes", "SQLite"],
      ["secrets", "secretsdb", "Reads", "SQLite"],
      ["memory", "llm", "Summarises with", "HTTPS"],
    ],
    note: "A project secret's decrypted value is never a satay task argument or return value (ADR-0006). A satay control call made by the daemon itself goes through asyncio.to_thread, because both share one event loop (ADR-0009).",
  },

  {
    id: "c4-3b-access",
    title: "3b. Fleet daemon: access, devices and sessions",
    dir: "DOWN",
    nodes: [
      { id: "operator", kind: "person", name: "Operator", desc: "PC dashboard" },
      { id: "phone", kind: "ext-container", name: "Paired browser", tech: "Dashboard on a phone", desc: "Holds its own device key" },
      { id: "relayc", kind: "ext-container", name: "Relay", tech: "FastAPI", desc: "Passes encrypted bytes" },
      { id: "claude", kind: "ext-container", name: "Agent session", tech: "tmux, claude", desc: "Runs the hook script" },
      {
        id: "daemon", kind: "boundary", name: "Fleet daemon", tech: "Container",
        children: [
          { id: "http", kind: "component", name: "HTTP surface", tech: "fleet/server.py", desc: "Projects, requests, devices, secrets, artifacts, the terminal websocket and the hook route." },
          { id: "guards", kind: "component", name: "Auth guards", tech: "fleet/auth.py, serve_token.py", desc: "Loopback token, or password sessions that slide forward while used. Chosen by the bind." },
          { id: "link", kind: "component", name: "Relay link", tech: "fleet/relay_link.py", desc: "The daemon's relay key, pairing, and the guard that applies a device's tier. The third security check." },
          { id: "conn", kind: "component", name: "Relay connector", tech: "relay/connector.py", desc: "Dials out, runs a Noise handshake per browser, calls the daemon's own app in process." },
          { id: "tiers", kind: "component", name: "Tiers", tech: "fleet/tiers.py", desc: "What each paired device may do, per route. A route with no tier is Admin only." },
          { id: "devs", kind: "component", name: "Device store and pairing", tech: "fleet/devices.py", desc: "Paired devices, the one-time pairing secret, the six-digit confirm on the PC, revocation." },
          { id: "proto", kind: "component", name: "Relay protocol", tech: "relay/protocol/", desc: "Noise IKpsk2, frames, streams. Imports nothing else from cuttlefish." },
          { id: "sessions", kind: "component", name: "Session manager", tech: "sessions/manager.py, tmux.py", desc: "One tmux session per project on a socket of its own. Starts, reattaches, relaunches." },
          { id: "term", kind: "component", name: "Terminal bridge", tech: "sessions/terminal.py", desc: "A pty running tmux attach, bridged to a websocket. The first message carries the token." },
          { id: "hooks", kind: "component", name: "Claude hook handler", tech: "fleet/claude_hooks.py, hook_files.py", desc: "Decides a PermissionRequest like a stream-json one and holds it until a person answers." },
          { id: "needs", kind: "component", name: "Needs you broker", tech: "requests.py", desc: "The inbox of requests agents raised, shared with the team view." },
        ],
      },
      { id: "devicesdb", kind: "ext-db", name: "Device store", tech: "SQLite", desc: "~/.cuttlefish/devices.db" },
    ],
    edges: [
      ["operator", "http", "Uses, same origin", "HTTP, websocket"],
      ["phone", "relayc", "Sends encrypted requests to", "wss"],
      ["relayc", "conn", "Passes bytes to", "ws"],
      ["conn", "proto", "Uses", ""],
      ["conn", "http", "Calls in process, with an internal token", ""],
      ["http", "guards", "Checks each request with", ""],
      ["http", "link", "Checks relayed requests with", ""],
      ["link", "tiers", "Looks the route up in", ""],
      ["link", "devs", "Finds the device in", ""],
      ["devs", "devicesdb", "Reads and writes", "SQLite"],
      ["http", "sessions", "Starts and stops", ""],
      ["http", "term", "Opens a terminal on", ""],
      ["term", "sessions", "Attaches to", ""],
      ["claude", "http", "Hook script posts to /api/hooks/claude-code", "HTTP, hook token"],
      ["http", "hooks", "Hands each hook to", ""],
      ["hooks", "needs", "Raises a request in", ""],
    ],
    note: "A device is trusted only after its first transport message decrypts, and a device the owner has not confirmed on the PC is served nothing (ADR-0037, ADR-0039). A held hook is released at shutdown, so uvicorn is not left waiting on a person (ADR-0040).",
  },

  {
    id: "c4-4-deployment",
    title: "4. Deployment: Tailscale https",
    dir: "DOWN",
    nodes: [
      {
        id: "phone", kind: "deploy", name: "Phone", tech: "iOS or Android, on the tailnet",
        children: [
          {
            id: "browser", kind: "deploy", name: "Browser", tech: "Chrome or Safari",
            children: [
              { id: "page", kind: "container", name: "Dashboard page", tech: "Svelte, WebCrypto", desc: "Opened from the pairing link. Holds the device key in IndexedDB." },
            ],
          },
        ],
      },
      {
        id: "pc", kind: "deploy", name: "PC", tech: "Linux, WSL2 or macOS",
        children: [
          {
            id: "tsd", kind: "deploy", name: "tailscaled", tech: "tailscale serve ends TLS here",
            children: [
              { id: "serve", kind: "container", name: "Serve forwards", tech: "https :443 and :8443", desc: "443 to the dashboard, 8443 to the relay as wss. Holds the certificate." },
            ],
          },
          {
            id: "loop", kind: "deploy", name: "Loopback only", tech: "127.0.0.1",
            children: [
              { id: "daemon", kind: "container", name: "Fleet daemon", tech: "cuttlefish serve --tailscale-https, :8431", desc: "Password sessions. Allows exactly the https name as an Origin." },
              { id: "relay", kind: "container", name: "Relay", tech: "cuttlefish relay, :8432", desc: "Headless. The daemon dials it as ws." },
              { id: "agent", kind: "container", name: "Agent session", tech: "tmux", desc: "On cuttlefish's own socket." },
            ],
          },
        ],
      },
      {
        id: "ts", kind: "deploy", name: "Tailscale", tech: "Control plane",
        children: [
          { id: "certs", kind: "container", name: "Certificates", tech: "Let's Encrypt via Tailscale", desc: "Issues the name's certificate. The name goes into public certificate logs." },
        ],
      },
    ],
    edges: [
      ["page", "serve", "Opens the page, then dials the relay", "https, wss over WireGuard"],
      ["serve", "daemon", "Forwards the page and API to", "http, 127.0.0.1:8431"],
      ["serve", "relay", "Forwards the relay websocket to", "http upgrade, 127.0.0.1:8432"],
      ["daemon", "relay", "Dials out to", "ws, 127.0.0.1:8432"],
      ["daemon", "agent", "Manages", "tmux"],
      ["serve", "certs", "Fetches a certificate from", "HTTPS"],
    ],
    note: "Plain cuttlefish serve --tailscale binds the tailnet address over http (ADR-0013): a phone can sign in with the password but cannot pair, because browsers withhold WebCrypto from a plain-http page.",
  },
];
