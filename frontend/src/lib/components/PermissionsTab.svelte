<script lang="ts">
  import type { FleetClient, PermissionMode, PermissionsCatalog, ProjectSummary } from "../api";
  import {
    accessLabel,
    addCommand,
    allowToLines,
    parseCommand,
    removeCommand,
    sameSet,
    togglePreset,
  } from "../team";
  import Icon from "./Icon.svelte";

  let {
    client,
    project,
    onChanged,
  }: { client: FleetClient; project: ProjectSummary; onChanged: () => Promise<void> } = $props();

  const MODE_ICONS: Record<string, string> = { "ask-first": "chat", standard: "check", auto: "bolt" };

  let catalog = $state<PermissionsCatalog | null>(null);
  let loadError = $state<string | null>(null);

  // The draft: what the screen shows and what Save would write. The saved values are the
  // project's own; the draft is reset to them after a save or a discard.
  // Read through a closure on purpose: the draft starts from the saved values, then lives on
  // its own until Save or Discard.
  const saved0 = () => project;
  let mode = $state<PermissionMode>(saved0().mode);
  let presets = $state<string[]>([...saved0().presets]);
  let extras = $state<string[]>(allowToLines(saved0().allow));
  let commandText = $state("");
  let saving = $state(false);
  let saveError = $state<string | null>(null);
  let saved = $state(false);

  const savedExtras = $derived(allowToLines(project.allow));
  const dirty = $derived(
    mode !== project.mode ||
      !sameSet(presets, project.presets) ||
      !sameSet(extras, savedExtras),
  );
  const catalogueOrder = $derived(catalog?.presets.map((preset) => preset.name) ?? []);

  $effect(() => {
    client.listPermissions().then(
      (result) => (catalog = result),
      () => (loadError = "Couldn't load the permission options. Check that the daemon is still running."),
    );
  });

  function discard() {
    mode = project.mode;
    presets = [...project.presets];
    extras = allowToLines(project.allow);
    commandText = "";
    saveError = null;
    saved = false;
  }

  function addTyped(event: SubmitEvent) {
    event.preventDefault();
    extras = addCommand(extras, commandText);
    commandText = "";
  }

  async function save() {
    saving = true;
    saveError = null;
    saved = false;
    try {
      if (mode !== project.mode) await client.updateMode(project.id, mode);
      if (!sameSet(presets, project.presets)) await client.updatePresets(project.id, presets);
      if (!sameSet(extras, savedExtras)) {
        const argv = extras.map((line) => parseCommand(line) ?? []);
        await client.updateAllow(project.id, argv.filter((command) => command.length > 0));
      }
      await onChanged();
      saved = true;
    } catch {
      saveError = "Couldn't save those changes. Check that the daemon is still running, then try again.";
    } finally {
      saving = false;
    }
  }
</script>

<div class="layout">
  <div class="main">
    {#if project.running}
      <p class="banner" role="status">
        A team is running. Changes apply the next time you start it, not to the one running now.
      </p>
    {/if}
    {#if loadError}
      <p class="error" role="alert">{loadError}</p>
    {/if}

    <section aria-labelledby="mode-heading">
      <h2 id="mode-heading" class="title-large">How much can agents do on their own?</h2>
      <p class="body-medium muted">Roles can override this on the Team tab.</p>
      <div class="modes" role="radiogroup" aria-labelledby="mode-heading">
        {#each catalog?.modes ?? [] as entry (entry.name)}
          <button
            type="button"
            role="radio"
            aria-checked={entry.name === mode}
            class="opt mode"
            class:selected={entry.name === mode}
            onclick={() => (mode = entry.name)}
          >
            <span class="mode-head">
              <span class="radio"></span>
              <Icon name={MODE_ICONS[entry.name] ?? "check"} size={18} />
              <span class="title-medium">{entry.title}</span>
              {#if entry.name === "standard"}<span class="tag attn">Default</span>{/if}
            </span>
            <span class="body-small">{entry.summary}</span>
          </button>
        {/each}
      </div>
    </section>

    <section aria-labelledby="commands-heading">
      <h2 id="commands-heading" class="title-large">Commands that run without asking</h2>
      <p class="body-medium muted">
        These run in Standard mode. Ask first runs none of them and Auto allows more. A read-only
        role only ever gets the first two groups, whatever is switched on here.
      </p>
      <div class="card groups">
        {#each catalog?.presets ?? [] as preset (preset.name)}
          {@const on = presets.includes(preset.name)}
          <div class="group">
            <button
              type="button"
              class="switch"
              role="switch"
              aria-checked={on}
              aria-label={preset.title}
              onclick={() => (presets = togglePreset(presets, preset.name, !on, catalogueOrder))}
            ></button>
            <div class="group-body">
              <span class="title-small" class:muted={!on}>{preset.title}</span>
              <span class="body-small muted">{preset.summary}</span>
              <div class="cmds">
                {#each preset.commands as command (command)}
                  <span class="cmd">{command}</span>
                {/each}
              </div>
            </div>
          </div>
        {/each}
        <div class="group extras">
          <span class="extras-icon"><Icon name="plus" /></span>
          <div class="group-body">
            <span class="title-small">Your own commands</span>
            <span class="body-small muted">
              A command and everything after it, for example <span class="mono">go test</span>.
            </span>
            {#if extras.length > 0}
              <div class="cmds">
                {#each extras as line (line)}
                  <span class="cmd removable">
                    {line}
                    <button
                      type="button"
                      class="remove"
                      aria-label="Remove {line}"
                      onclick={() => (extras = removeCommand(extras, line))}
                    >
                      <Icon name="trash" size={14} />
                    </button>
                  </span>
                {/each}
              </div>
            {/if}
            <form class="add" onsubmit={addTyped}>
              <label class="field">
                <span class="field-label">Add a command</span>
                <input class="mono" bind:value={commandText} placeholder="go test" spellcheck="false" />
              </label>
              <button type="submit" class="btn btn-tonal" disabled={!commandText.trim()}>Add</button>
            </form>
          </div>
        </div>
      </div>
    </section>
  </div>

  <aside class="side">
    <section class="card filled panel">
      <h2 class="title-medium heading"><Icon name="lock" />Always blocked</h2>
      <p class="body-small muted">No mode, rule or answer overrides these.</p>
      <ul class="blocked">
        {#each catalog?.never_allowed ?? [] as item (item.label)}
          <li>
            <span class="cmd">{item.label}</span>
            <span class="body-small muted">{item.summary}</span>
          </li>
        {/each}
      </ul>
    </section>

    <section class="card panel">
      <h2 class="title-medium">How each agent receives this</h2>
      {#each catalog?.backends ?? [] as backend, index (backend.name)}
        {#if index > 0}<hr class="divider" />{/if}
        <div class="backend">
          <div class="backend-head">
            <span class="title-small">{backend.name}</span>
            <span class="tag" class:primary={backend.name === "kopicode"}>{backend.when}</span>
          </div>
          <span class="body-small muted">{backend.summary}</span>
        </div>
      {/each}
    </section>

    <section class="card panel">
      <h2 class="title-medium">Role overrides</h2>
      {#each project.roles as role (role.name)}
        <div class="override">
          <span class="title-small">{role.name}</span>
          <span class="tag" class:primary={role.access === "read-only"}>
            {accessLabel(role.access, project.mode)}
          </span>
        </div>
      {:else}
        <p class="body-small muted">This project has no roles yet. Add them on the Team tab.</p>
      {/each}
    </section>

    {#if saveError}
      <p class="error" role="alert">{saveError}</p>
    {/if}
    {#if saved && !dirty}
      <p class="body-small muted" role="status">Saved.</p>
    {/if}
    <div class="actions">
      <button type="button" class="btn btn-text" disabled={!dirty || saving} onclick={discard}>
        Discard changes
      </button>
      <button type="button" class="btn btn-filled" disabled={!dirty || saving} onclick={save}>
        {saving ? "Saving…" : "Save permissions"}
      </button>
    </div>
  </aside>
</div>

<style>
  .layout {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 23rem;
    gap: 2rem;
    align-items: start;
  }

  .main,
  .side {
    display: flex;
    flex-direction: column;
    gap: 1.75rem;
    min-width: 0;
  }

  .side {
    gap: 1rem;
  }

  section {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
  }

  h2,
  p {
    margin: 0;
  }

  .banner {
    padding: 10px 14px;
    border-radius: var(--md-sys-shape-corner-small);
    background: var(--md-sys-color-attention-container);
    color: var(--md-sys-color-on-attention-container);
    font-size: 0.875rem;
  }

  .error {
    color: var(--md-sys-color-error);
    font-size: 0.875rem;
  }

  .modes {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr));
    gap: 12px;
  }

  .mode {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .mode-head {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .mode-head .tag {
    margin-left: auto;
  }

  .groups {
    overflow: hidden;
  }

  .group {
    display: flex;
    gap: 16px;
    align-items: flex-start;
    padding: 16px 20px;
    border-bottom: 1px solid var(--md-sys-color-outline-variant);
  }

  .group:last-child {
    border-bottom: 0;
  }

  .group-body {
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-width: 0;
    flex: 1;
  }

  .extras-icon {
    width: 52px;
    flex: none;
    display: grid;
    place-items: center;
    color: var(--md-sys-color-on-surface-variant);
    height: 32px;
  }

  .cmds {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  .cmd {
    font-family: var(--md-ref-typeface-mono);
    font-size: 0.75rem;
    line-height: 1rem;
    padding: 3px 8px;
    border-radius: var(--md-sys-shape-corner-extra-small);
    background: var(--md-sys-color-surface-container-high);
    color: var(--md-sys-color-on-surface);
    white-space: nowrap;
  }

  .cmd.removable {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }

  .remove {
    all: unset;
    display: inline-grid;
    place-items: center;
    cursor: pointer;
    color: var(--md-sys-color-on-surface-variant);
    border-radius: 50%;
    width: 18px;
    height: 18px;
  }

  .remove:hover {
    color: var(--md-sys-color-error);
  }

  .remove:focus-visible {
    outline: 2px solid var(--md-sys-color-primary);
  }

  .add {
    display: flex;
    gap: 12px;
    align-items: center;
    margin-top: 12px;
  }

  .add .field {
    flex: 1;
    min-width: 0;
  }

  .add :global(.field-label) {
    background: var(--md-sys-color-surface-container-lowest);
  }

  .panel {
    padding: 20px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .heading {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .heading :global(svg) {
    color: var(--md-sys-color-error);
  }

  .blocked {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .blocked li {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 2px;
  }

  .backend {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .backend-head,
  .override {
    display: flex;
    align-items: center;
    gap: 8px;
    justify-content: space-between;
  }

  .actions {
    display: flex;
    gap: 8px;
    justify-content: flex-end;
  }

  @media (max-width: 960px) {
    .layout {
      grid-template-columns: minmax(0, 1fr);
    }
  }
</style>
