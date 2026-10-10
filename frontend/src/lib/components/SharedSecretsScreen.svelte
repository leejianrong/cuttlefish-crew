<script lang="ts">
  import type { FleetClient, SharedSecrets } from "../api";
  import {
    CREDENTIAL_NAMES,
    credentialNote,
    overrideTag,
    sharedRemovalNote,
  } from "../secrets";
  import Icon from "./Icon.svelte";
  import SecretDialog, { type SecretDialogState } from "./SecretDialog.svelte";

  let { client }: { client: FleetClient } = $props();

  let data = $state<SharedSecrets | null>(null);
  let failed = $state(false);
  let status = $state<string | null>(null);
  let dialog = $state<SecretDialogState | null>(null);
  let copied = $state(false);

  const KEY_COMMAND = "cuttlefish secrets generate-key";

  async function load() {
    try {
      data = await client.listSharedSecrets();
      failed = false;
    } catch {
      failed = true;
    }
  }

  $effect(() => {
    load();
  });

  const rows = $derived(data?.shared ?? []);
  const suggestions = $derived(CREDENTIAL_NAMES.filter((name) => !rows.some((r) => r.name === name)));
  const removing = $derived(rows.find((row) => row.name === dialog?.name));

  function open(kind: SecretDialogState["kind"], name = "") {
    dialog = { kind, name };
    status = null;
  }

  async function save(name: string, value: string) {
    const replacing = rows.some((row) => row.name === name);
    await client.setSharedSecret(name, value);
    status = `${replacing ? "Replaced" : "Saved"} ${name} for every project. It applies the next time a team starts.`;
    await load();
  }

  async function remove(name: string) {
    await client.deleteSharedSecret(name);
    status = `Removed ${name}. Projects without their own value lose it the next time their team starts.`;
    await load();
  }

  async function copyCommand() {
    try {
      await navigator.clipboard.writeText(KEY_COMMAND);
      copied = true;
    } catch {
      copied = false;
    }
  }
</script>

<div class="page">
  <header class="head">
    <div>
      <h1 class="headline-medium">Secrets</h1>
      <p class="body-large muted">
        Shared with every project. Values can be replaced, never shown. A project's own secret of
        the same name wins over the shared one.
      </p>
    </div>
    {#if data?.enabled}
      <button class="btn btn-filled" type="button" onclick={() => open("add")}>
        <Icon name="plus" size={18} />Add shared secret
      </button>
    {/if}
  </header>

  {#if failed}
    <p class="body-medium muted" role="status">Couldn't read the secrets. Check that the daemon is still running.</p>
  {:else if data === null}
    <p class="body-medium muted" role="status">Reading…</p>
  {:else if !data.enabled}
    <div class="note" role="status">
      <strong>Secrets are switched off.</strong>
      The daemon has no secrets key, so nothing can be stored yet.
    </div>
    <div class="card filled panel">
      <h2 class="title-medium">Turn secrets on</h2>
      <ol class="steps body-medium">
        <li>
          Make a key. It prints once:
          <span class="cmd">
            <code class="mono">{KEY_COMMAND}</code>
            <button class="btn btn-text sm" type="button" onclick={copyCommand}>
              {copied ? "Copied" : "Copy"}
            </button>
          </span>
        </li>
        <li>Set it as <code class="mono">CUTTLEFISH_SECRETS_KEY</code> in the environment of <code class="mono">cuttlefish serve</code>.</li>
        <li>Restart <code class="mono">cuttlefish serve</code> and reload this page.</li>
      </ol>
      <p class="body-small muted">
        Keep the key somewhere safe. Losing it loses every secret it protects.
      </p>
    </div>
  {:else}
    {#if status}<p class="status body-medium" role="status">{status}</p>{/if}
    {#if rows.length === 0}
      <div class="card filled panel">
        <h2 class="title-medium">No shared secrets yet</h2>
        <p class="body-medium muted">
          Add a key every project may use, such as a personal OpenRouter key. A single project's own
          secrets are on that project's Secrets tab.
        </p>
      </div>
    {:else}
      <div class="card filled list">
        <div class="list-head">
          <span class="title-small">Shared with every project</span>
          <span class="label-medium muted">{rows.length} set</span>
        </div>
        <ul>
          {#each rows as row (row.name)}
            <li class="row">
              <span class="lock"><Icon name="lock" size={20} /></span>
              <div class="what">
                <span class="name mono">{row.name}</span>
                <span class="sub">
                  <span class="sealed" role="img" aria-label="Value hidden">••••••••••••</span>
                  {#if row.kind === "credential"}<span class="tag primary">Agent key</span>{/if}
                  {#if overrideTag(row.overridden_in)}
                    <span class="tag">{overrideTag(row.overridden_in)}</span>
                  {/if}
                </span>
                {#if row.kind === "credential"}
                  <span class="body-small muted">{credentialNote(row.name, data?.broker)}</span>
                {/if}
              </div>
              <div class="acts">
                <button class="btn btn-text sm" type="button" aria-label="Replace {row.name}" onclick={() => open("replace", row.name)}>Replace</button>
                <button class="btn btn-danger-text sm" type="button" aria-label="Remove {row.name}" onclick={() => open("remove", row.name)}>Remove</button>
              </div>
            </li>
          {/each}
        </ul>
      </div>
      <p class="body-small muted">
        Removing a shared secret takes it from every project that does not have its own. The dialog
        says how many that is before you confirm.
      </p>
    {/if}
  {/if}
</div>

{#if dialog}
  <SecretDialog
    {dialog}
    existing={rows.map((row) => row.name)}
    {suggestions}
    nameHelp="Capital letters, digits and underscores. Shared with every project that has no secret of the same name."
    removeNote={sharedRemovalNote(removing?.lost_by ?? [])}
    onSave={save}
    onRemove={remove}
    onClose={() => (dialog = null)}
  />
{/if}

<style>
  .page {
    max-width: 72rem;
    margin: 0 auto;
    padding: 2rem 1.5rem 4rem;
    display: flex;
    flex-direction: column;
    gap: 1rem;
  }

  .head {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px;
  }

  h1,
  h2,
  p {
    margin: 0;
  }

  .head p {
    max-width: 65ch;
    margin-top: 4px;
  }

  .status {
    padding: 10px 16px;
    border-radius: var(--md-sys-shape-corner-small);
    background: var(--md-sys-color-secondary-container);
    color: var(--md-sys-color-on-secondary-container);
  }

  .note {
    padding: 14px 16px;
    border-radius: var(--md-sys-shape-corner-medium);
    background: var(--md-sys-color-tertiary-container);
    color: var(--md-sys-color-on-tertiary-container);
  }

  .panel {
    padding: 20px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    align-items: flex-start;
    max-width: 56rem;
  }

  .steps {
    margin: 0;
    padding-left: 1.25rem;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .cmd {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 6px;
    padding: 6px 6px 6px 12px;
    border-radius: var(--md-sys-shape-corner-small);
    background: var(--md-sys-color-surface-container-highest);
    max-width: 100%;
  }

  .cmd code {
    overflow-wrap: anywhere;
    flex: 1;
    min-width: 0;
  }

  .list {
    overflow: hidden;
    max-width: 56rem;
  }

  .list-head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 8px;
    padding: 14px 16px 6px;
  }

  ul {
    list-style: none;
    margin: 0;
    padding: 0;
  }

  .row {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto;
    gap: 12px;
    align-items: center;
    padding: 8px 8px 8px 16px;
    border-top: 1px solid var(--md-sys-color-outline-variant);
  }

  .lock {
    color: var(--md-sys-color-primary);
    display: inline-flex;
  }

  .what {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }

  .name {
    font-weight: 500;
    overflow-wrap: anywhere;
  }

  .sub {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
  }

  .sealed {
    font-family: var(--md-ref-typeface-mono);
    letter-spacing: 0.2em;
    color: var(--md-sys-color-on-surface-variant);
  }

  .acts {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    justify-content: flex-end;
  }

  @media (max-width: 560px) {
    .row {
      grid-template-columns: auto minmax(0, 1fr);
    }
    .acts {
      grid-column: 2;
      justify-content: flex-start;
    }
  }
</style>
