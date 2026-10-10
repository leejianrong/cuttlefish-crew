<script lang="ts">
  import type { FleetClient, ProjectSecrets, ProjectSummary, SecretRow } from "../api";
  import {
    credentialNote,
    secretFailure,
    secretNameProblem,
    secretValueProblem,
    suggestedNames,
  } from "../secrets";
  import Icon from "./Icon.svelte";

  let {
    client,
    project,
    active,
  }: {
    client: FleetClient;
    project: ProjectSummary;
    /** Whether this tab is showing: it reads again each time it opens. */
    active: boolean;
  } = $props();

  let data = $state<ProjectSecrets | null>(null);
  let failed = $state(false);
  let status = $state<string | null>(null);

  type Dialog = { kind: "add" | "replace" | "remove"; name: string };
  let dialog = $state<Dialog | null>(null);
  let dialogEl = $state<HTMLDialogElement | null>(null);
  // What is typed. The value lives only here and is cleared whenever the dialog closes.
  let nameInput = $state("");
  let valueInput = $state("");
  let nameProblem = $state<string | null>(null);
  let valueProblem = $state<string | null>(null);
  let saveProblem = $state<string | null>(null);
  let busy = $state(false);
  let copied = $state(false);

  const KEY_COMMAND = "cuttlefish secrets generate-key";

  async function load() {
    const id = project.id;
    try {
      const next = await client.listProjectSecrets(id);
      if (id === project.id) {
        data = next;
        failed = false;
      }
    } catch {
      if (id === project.id) failed = true;
    }
  }

  $effect(() => {
    void project.id;
    if (active) load();
  });

  const own = $derived(data?.project ?? []);
  // A shared secret the project overrides is shown once, as the project's own.
  const shared = $derived(
    (data?.shared ?? []).filter((row) => !own.some((mine) => mine.name === row.name)),
  );
  const suggestions = $derived(
    suggestedNames(project, [...own, ...(data?.shared ?? [])].map((row) => row.name)),
  );
  const overrides = (row: SecretRow) => (data?.shared ?? []).some((s) => s.name === row.name);

  function open(kind: Dialog["kind"], name = "") {
    dialog = { kind, name };
    nameInput = name;
    valueInput = "";
    nameProblem = valueProblem = saveProblem = null;
    status = null;
  }

  $effect(() => {
    if (dialog && dialogEl && !dialogEl.open) dialogEl.showModal();
  });

  function closeDialog() {
    dialogEl?.close();
  }

  function onClosed() {
    dialog = null;
    valueInput = "";
  }

  async function save(event: SubmitEvent) {
    event.preventDefault();
    if (!dialog || busy) return;
    const name = dialog.kind === "replace" ? dialog.name : nameInput.trim();
    nameProblem = dialog.kind === "replace" ? null : secretNameProblem(name);
    valueProblem = secretValueProblem(valueInput);
    saveProblem = null;
    if (nameProblem || valueProblem) return;
    busy = true;
    try {
      const replacing = own.some((row) => row.name === name);
      await client.setProjectSecret(project.id, name, valueInput);
      closeDialog();
      status = `${replacing ? "Replaced" : "Saved"} ${name}. It applies the next time the team starts.`;
      await load();
    } catch (error) {
      saveProblem = secretFailure(error);
    } finally {
      busy = false;
    }
  }

  async function remove() {
    if (!dialog || busy) return;
    const name = dialog.name;
    busy = true;
    try {
      await client.deleteProjectSecret(project.id, name);
      closeDialog();
      status = `Removed ${name}. Agents lose it the next time the team starts.`;
      await load();
    } catch (error) {
      saveProblem = secretFailure(error);
    } finally {
      busy = false;
    }
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

<section class="secrets" aria-labelledby="secrets-heading">
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
      <h2 id="secrets-heading" class="title-medium">Turn secrets on</h2>
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
    <div class="head">
      <h2 id="secrets-heading" class="title-medium">Secrets for {project.name}</h2>
      <button class="btn btn-filled" type="button" onclick={() => open("add")}>
        <Icon name="plus" size={18} />Add secret
      </button>
    </div>
    <p class="body-medium muted info">
      Secrets reach every role in this project as environment variables. Values can be replaced,
      never shown. Changes apply the next time the team starts.
    </p>
    {#if status}<p class="status body-medium" role="status">{status}</p>{/if}

    {#if own.length === 0 && shared.length === 0}
      <div class="card filled panel empty">
        <h3 class="title-medium">No secrets for {project.name} yet</h3>
        <p class="body-medium muted">
          Add the credentials its agents need, such as an API key.
        </p>
      </div>
    {:else}
      <div class="card filled list">
        <div class="list-head">
          <span class="title-small">This project</span>
          <span class="label-medium muted">{own.length} set</span>
        </div>
        {#if own.length === 0}
          <p class="row body-medium muted">This project has none of its own.</p>
        {/if}
        <ul>
          {#each own as row (row.name)}
            <li class="row">
              <span class="lock"><Icon name="lock" size={20} /></span>
              <div class="what">
                <span class="name mono">{row.name}</span>
                <span class="sub">
                  <span class="sealed" role="img" aria-label="Value hidden">••••••••••••</span>
                  {#if row.kind === "credential"}<span class="tag primary">Agent key</span>{/if}
                  {#if overrides(row)}<span class="tag">Wins over the shared one</span>{/if}
                </span>
                {#if row.kind === "credential"}
                  <span class="body-small muted">{credentialNote(row.name)}</span>
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
      {#if shared.length > 0}
        <div class="card filled list">
          <div class="list-head">
            <span class="title-small">Shared with every project</span>
            <span class="label-medium muted">read-only here</span>
          </div>
          <ul>
            {#each shared as row (row.name)}
              <li class="row">
                <span class="lock"><Icon name="lock" size={20} /></span>
                <div class="what">
                  <span class="name mono">{row.name}</span>
                  <span class="sub">
                    <span class="sealed" role="img" aria-label="Value hidden">••••••••••••</span>
                    <span class="tag">Shared</span>
                    {#if row.kind === "credential"}<span class="tag primary">Agent key</span>{/if}
                  </span>
                  {#if row.kind === "credential"}
                    <span class="body-small muted">{credentialNote(row.name)}</span>
                  {/if}
                </div>
              </li>
            {/each}
          </ul>
        </div>
      {/if}
    {/if}
  {/if}
</section>

<dialog bind:this={dialogEl} onclose={onClosed} aria-labelledby="secret-dialog-title">
  {#if dialog?.kind === "remove"}
    <h3 id="secret-dialog-title" class="headline-small">Remove {dialog.name}?</h3>
    <p class="body-medium muted">
      Agents lose it the next time the team starts. This cannot be undone, and the value cannot be
      shown again.
    </p>
    {#if saveProblem}<p class="body-small error" role="alert">{saveProblem}</p>{/if}
    <div class="actions">
      <button class="btn btn-text" type="button" onclick={closeDialog}>Keep it</button>
      <button class="btn btn-danger" type="button" disabled={busy} onclick={remove}>
        {busy ? "Removing…" : "Remove secret"}
      </button>
    </div>
  {:else if dialog}
    <h3 id="secret-dialog-title" class="headline-small">
      {dialog.kind === "replace" ? `Replace ${dialog.name}` : "Add a secret"}
    </h3>
    <form onsubmit={save} novalidate>
      <label class="field">
        <span class="field-label">Name</span>
        <input
          class="mono"
          bind:value={nameInput}
          readonly={dialog.kind === "replace"}
          autocomplete="off"
          spellcheck="false"
          aria-invalid={nameProblem ? "true" : undefined}
          aria-describedby="secret-name-help"
        />
      </label>
      <p id="secret-name-help" class="body-small" class:error={nameProblem} class:muted={!nameProblem}>
        {nameProblem ??
          (dialog.kind === "add" && own.some((row) => row.name === nameInput.trim())
            ? `Saving replaces the value already set for ${nameInput.trim()}.`
            : "Capital letters, digits and underscores, like GITHUB_TOKEN.")}
      </p>
      {#if dialog.kind === "add" && suggestions.length > 0 && nameInput === ""}
        <div class="suggest">
          <span class="label-medium muted">Suggested for this project's agents</span>
          {#each suggestions as name (name)}
            <button class="chip" type="button" onclick={() => (nameInput = name)}>
              <span class="mono">{name}</span>
            </button>
          {/each}
        </div>
      {/if}
      <label class="field value">
        <span class="field-label">Value</span>
        <input
          type="password"
          bind:value={valueInput}
          autocomplete="new-password"
          spellcheck="false"
          aria-invalid={valueProblem ? "true" : undefined}
          aria-describedby="secret-value-help"
        />
      </label>
      <p id="secret-value-help" class="body-small" class:error={valueProblem} class:muted={!valueProblem}>
        {valueProblem ??
          (dialog.kind === "replace"
            ? "The current value cannot be shown. Saving replaces it."
            : "Pasted here, encrypted on save, never shown again.")}
      </p>
      {#if saveProblem}<p class="body-small error" role="alert">{saveProblem}</p>{/if}
      <div class="actions">
        <button class="btn btn-text" type="button" onclick={closeDialog}>Cancel</button>
        <button class="btn btn-filled" type="submit" disabled={busy}>
          {busy ? "Saving…" : "Save secret"}
        </button>
      </div>
    </form>
  {/if}
</dialog>

<style>
  .secrets {
    display: flex;
    flex-direction: column;
    gap: 12px;
    max-width: 56rem;
  }

  .head {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
  }

  h2,
  h3,
  p {
    margin: 0;
  }

  .info {
    max-width: 65ch;
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

  p.row {
    display: block;
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

  .error {
    color: var(--md-sys-color-error);
  }

  dialog {
    border: 0;
    border-radius: var(--md-sys-shape-corner-extra-large);
    background: var(--md-sys-color-surface-container-high);
    color: var(--md-sys-color-on-surface);
    padding: 24px;
    width: min(30rem, calc(100vw - 32px));
    box-shadow: var(--md-sys-elevation-level2);
  }

  /* The outlined field's label cuts the border; it must match the dialog, not the page. */
  dialog :global(.field-label) {
    background: var(--md-sys-color-surface-container-high);
  }

  dialog :global(.field:has([aria-invalid="true"])) {
    border-color: var(--md-sys-color-error);
  }

  dialog::backdrop {
    background: color-mix(in srgb, var(--md-sys-color-scrim) 32%, transparent);
  }

  dialog[open],
  form {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .suggest {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
  }

  .value {
    margin-top: 4px;
  }

  .actions {
    display: flex;
    justify-content: flex-end;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 8px;
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
