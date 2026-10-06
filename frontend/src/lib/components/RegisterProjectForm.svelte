<script lang="ts">
  import type { FleetClient, RoleDefinition } from "../api";

  let { client, onRegistered }: { client: FleetClient; onRegistered: () => void } = $props();

  let open = $state(false);
  let name = $state("");
  let root = $state("");
  let rolesText = $state("builder: ships fast, terse commits\nreviewer: skeptical, flags risk");
  let allowText = $state("");
  let maxTokensText = $state("");
  let maxCostUsdText = $state("");
  let backend = $state("");
  let submitting = $state(false);
  let error = $state<string | null>(null);

  function parseRoles(text: string): RoleDefinition[] {
    return text
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        // `name@backend: persona` -- `@backend` optional (KAN-1809).
        const [head, ...rest] = line.split(":");
        const [roleName, roleBackend] = head.split("@");
        return {
          name: roleName.trim(),
          persona: rest.join(":").trim(),
          backend: roleBackend?.trim() || null,
        };
      })
      .filter((role) => role.name.length > 0);
  }

  function parseAllow(text: string): string[][] {
    // One shell command per line, split on whitespace -- unlike the CLI's own
    // `--allow`, this doesn't shell-quote a multi-word argument (e.g. a commit
    // message), a deliberate simplification for this form (ADR-0009's Q53).
    return text
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => line.split(/\s+/));
  }

  async function submit(event: SubmitEvent) {
    event.preventDefault();
    submitting = true;
    error = null;
    try {
      await client.registerProject({
        name,
        root,
        backend: backend || null,
        roles: parseRoles(rolesText),
        allow: parseAllow(allowText),
        max_tokens: maxTokensText.trim() ? Number(maxTokensText) : null,
        max_cost_usd: maxCostUsdText.trim() ? Number(maxCostUsdText) : null,
      });
      name = "";
      root = "";
      allowText = "";
      maxTokensText = "";
      maxCostUsdText = "";
      open = false;
      onRegistered();
    } catch {
      error = "couldn't register that project -- check the daemon's own log";
    } finally {
      submitting = false;
    }
  }
</script>

{#if !open}
  <button class="add-button" onclick={() => (open = true)}>+ Register a project</button>
{:else}
  <form onsubmit={submit}>
    <div class="row">
      <label>
        Name
        <input type="text" bind:value={name} placeholder="demo" required />
      </label>
      <label>
        Root
        <input type="text" bind:value={root} placeholder="/home/you/code/demo" required />
      </label>
    </div>
    <label>
      Default agent backend (optional)
      <select bind:value={backend}>
        <option value="">daemon default</option>
        <option value="kopicode">kopicode</option>
        <option value="claude-code">claude-code</option>
        <option value="codex">codex</option>
      </select>
    </label>
    <label>
      Roles (one per line, <code>name: persona</code> or <code>name@backend: persona</code>;
      persona and backend optional)
      <textarea bind:value={rolesText} rows="3"></textarea>
    </label>
    <label>
      Extra shell commands (one per line, e.g. <code>go test</code>). Tests, linters,
      builds and everyday git are already allowed.
      <textarea bind:value={allowText} rows="2"></textarea>
    </label>
    <div class="row">
      <label>
        Max tokens per role (KAN-1712, optional)
        <input type="number" min="0" bind:value={maxTokensText} placeholder="no ceiling" />
      </label>
      <label>
        Max cost per role, USD (optional)
        <input type="number" min="0" step="0.01" bind:value={maxCostUsdText} placeholder="no ceiling" />
      </label>
    </div>
    {#if error}
      <p class="error">{error}</p>
    {/if}
    <div class="actions">
      <button type="button" class="ghost" onclick={() => (open = false)}>Cancel</button>
      <button type="submit" disabled={submitting}>
        {submitting ? "Registering…" : "Register"}
      </button>
    </div>
  </form>
{/if}

<style>
  .add-button {
    background: none;
    border: 1px dashed var(--border);
    border-radius: 10px;
    color: var(--text-muted);
    padding: 0.9rem;
    width: 100%;
    text-align: left;
    font-weight: 500;
  }

  .add-button:hover {
    border-color: var(--accent);
    color: var(--accent);
  }

  form {
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1.25rem;
  }

  .row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1rem;
  }

  label {
    display: block;
    font-size: 0.8rem;
    color: var(--text-muted);
    margin-bottom: 0.9rem;
  }

  label code {
    color: var(--text);
  }

  input,
  textarea {
    display: block;
    width: 100%;
    margin-top: 0.35rem;
    padding: 0.5rem 0.65rem;
    background: var(--bg-inset);
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--text);
    resize: vertical;
  }

  .actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.6rem;
  }

  button[type="submit"] {
    padding: 0.5rem 1rem;
    border: none;
    border-radius: 8px;
    background: var(--accent);
    color: var(--accent-text);
    font-weight: 600;
  }

  button.ghost {
    padding: 0.5rem 1rem;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: none;
    color: var(--text-muted);
  }

  .error {
    color: var(--danger);
    font-size: 0.82rem;
  }
</style>
