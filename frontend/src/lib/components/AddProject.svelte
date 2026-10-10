<script lang="ts">
  import {
    FleetApiError,
    type FleetClient,
    type FolderInspection,
    type PermissionMode,
    type ProjectSummary,
    type TeamTemplate,
  } from "../api";
  import { folderName, parseAllow, parseLimit } from "../folders";
  import { radiogroup } from "../radiogroup";
  import { presetsForLanguages } from "../team";
  import FolderPicker from "./FolderPicker.svelte";
  import Icon from "./Icon.svelte";

  let {
    client,
    onCancel,
    onRegistered,
  }: {
    client: FleetClient;
    onCancel: () => void;
    onRegistered: (project: ProjectSummary) => void;
  } = $props();

  const MODES: { id: PermissionMode; label: string; icon: string; text: string }[] = [
    {
      id: "ask-first",
      label: "Ask first",
      icon: "chat",
      text: "Agents can edit files, but no command runs on its own. With kopicode or Codex each command pauses the agent until you allow or deny it; Claude Code can't pause, so there it is refused.",
    },
    {
      id: "standard",
      label: "Standard",
      icon: "check",
      text: "Agents run everyday dev commands on their own: tests, linters, builds, installs, and git add or commit. With kopicode or Codex anything else pauses the agent until you answer; Claude Code refuses it.",
    },
    {
      id: "auto",
      label: "Auto",
      icon: "bolt",
      text: "Any command runs without asking. sudo, force pushes, downloads piped into a shell and writes outside the project folder are always blocked.",
    },
  ];

  let selected = $state<string | null>(null);
  let browsePath = $state<string | undefined>(undefined);
  let inspection = $state<FolderInspection | null>(null);
  let templates = $state<TeamTemplate[]>([]);
  let template = $state("");
  let mode = $state<PermissionMode>("standard");
  let recent = $state<string[]>([]);
  let defaultPresets = $state<string[]>([]);

  let advancedOpen = $state(false);
  let name = $state("");
  let backend = $state("");
  let allowText = $state("");
  let maxTokensText = $state("");
  let maxCostText = $state("");

  let submitting = $state(false);
  let error = $state<string | null>(null);

  const modeText = $derived(MODES.find((entry) => entry.id === mode)?.text ?? "");
  const chosenTemplate = $derived(templates.find((entry) => entry.name === template));
  const projectName = $derived(name.trim() || (selected ? folderName(selected) : ""));
  const folderLabel = $derived(selected ? folderName(selected) : "");
  const startingPresets = $derived(
    inspection && defaultPresets.length > 0
      ? presetsForLanguages(inspection.languages, defaultPresets)
      : null,
  );

  $effect(() => {
    client.listTemplates().then(
      (result) => {
        templates = result.templates;
        if (!template) template = result.default;
      },
      () => {
        error = "Couldn't load the team templates. Check that the daemon is still running.";
      },
    );
    client.listPermissions().then(
      (result) => {
        defaultPresets = result.presets.filter((preset) => preset.default).map((p) => p.name);
      },
      () => {},
    );
    client.listProjects().then(
      (result) => {
        recent = [...new Set(result.projects.map((project) => project.root))].slice(0, 4);
      },
      () => {},
    );
  });

  let inspectTicket = 0;
  $effect(() => {
    const path = selected;
    inspection = null;
    if (!path) return;
    const ticket = ++inspectTicket;
    client.inspectFolder(path).then(
      (result) => {
        if (ticket === inspectTicket) inspection = result;
      },
      () => {},
    );
  });

  function choose(path: string) {
    selected = path;
    error = null;
  }

  function useRecent(path: string) {
    choose(path);
    browsePath = path.slice(0, path.lastIndexOf("/")) || "/";
  }

  async function submit(event: SubmitEvent) {
    event.preventDefault();
    if (!selected) {
      error = "Pick a folder first.";
      return;
    }
    const maxTokens = parseLimit(maxTokensText);
    const maxCost = parseLimit(maxCostText);
    if (maxTokens === "invalid" || maxCost === "invalid") {
      advancedOpen = true;
      error = "Spend limits must be plain positive numbers, or left blank.";
      return;
    }
    submitting = true;
    error = null;
    try {
      const project = await client.registerProject({
        name: projectName,
        root: selected,
        template,
        mode,
        ...(startingPresets ? { presets: startingPresets } : {}),
        backend: backend || null,
        allow: parseAllow(allowText),
        max_tokens: maxTokens,
        max_cost_usd: maxCost,
      });
      onRegistered(project);
    } catch (caught) {
      error =
        caught instanceof FleetApiError
          ? `The daemon refused that: ${caught.message}`
          : "Couldn't reach the daemon. Check that it is still running.";
    } finally {
      submitting = false;
    }
  }
</script>

<form class="page" onsubmit={submit}>
  <header>
    <h1 class="headline-medium">Add a project</h1>
    <p class="body-large muted">
      Pick a folder, choose a team, and go. Everything else already has a sensible default.
    </p>
  </header>

  <div class="layout">
    <div class="main">
      <section aria-labelledby="folder-heading">
        <div class="heading-row">
          <h2 id="folder-heading" class="title-large">Folder</h2>
          {#if recent.length > 0}
            <div class="recent">
              <span class="label-medium muted">Recent</span>
              {#each recent as path (path)}
                <button type="button" class="chip" onclick={() => useRecent(path)}>
                  {folderName(path)}
                </button>
              {/each}
            </div>
          {/if}
        </div>
        <FolderPicker {client} {selected} onSelect={choose} bind:browsePath />
      </section>

      <section aria-labelledby="team-heading">
        <h2 id="team-heading" class="title-large">Team</h2>
        <div class="templates" role="radiogroup" aria-labelledby="team-heading" use:radiogroup>
          {#each templates as entry (entry.name)}
            <button
              type="button"
              role="radio"
              aria-checked={entry.name === template}
              tabindex={entry.name === template ? 0 : -1}
              class="opt template"
              class:selected={entry.name === template}
              onclick={() => (template = entry.name)}
            >
              <span class="template-head">
                <span class="radio"></span>
                <span class="title-medium">{entry.title}</span>
              </span>
              <span class="body-small template-text">{entry.summary}</span>
              <span class="roles">
                {#each entry.roles as role (role)}
                  <span class="tag">{role}</span>
                {/each}
              </span>
            </button>
          {/each}
        </div>
      </section>

      <section aria-labelledby="mode-heading">
        <h2 id="mode-heading" class="title-large">What can the agents do without asking?</h2>
        <div class="seg" role="radiogroup" aria-labelledby="mode-heading" use:radiogroup>
          {#each MODES as entry (entry.id)}
            <button
              type="button"
              role="radio"
              aria-checked={entry.id === mode}
              tabindex={entry.id === mode ? 0 : -1}
              class:selected={entry.id === mode}
              onclick={() => (mode = entry.id)}
            >
              <Icon name={entry.icon} size={18} />{entry.label}
            </button>
          {/each}
        </div>
        <p class="body-medium muted mode-text">{modeText}</p>
      </section>

      <section>
        <button
          type="button"
          class="item card filled advanced-toggle"
          aria-expanded={advancedOpen}
          aria-controls="advanced"
          onclick={() => (advancedOpen = !advancedOpen)}
        >
          <Icon name={advancedOpen ? "chevron-down" : "chevron-right"} />
          <span class="advanced-text">
            <span class="title-medium">Advanced options</span>
            <span class="body-small muted">
              Name, agent backend, spend limits, custom commands
            </span>
          </span>
          <span class="tag">All optional</span>
        </button>
        {#if advancedOpen}
          <div id="advanced" class="advanced card filled">
            <label class="field">
              <span class="field-label">Project name</span>
              <input bind:value={name} placeholder={folderLabel || "Defaults to the folder name"} />
            </label>
            <label class="field">
              <span class="field-label">Agent backend</span>
              <select bind:value={backend}>
                <option value="">Daemon default</option>
                <option value="kopicode">kopicode</option>
                <option value="claude-code">Claude Code</option>
                <option value="codex">Codex</option>
              </select>
            </label>
            <label class="field wide">
              <span class="field-label">Extra commands the agents may run (one per line)</span>
              <textarea bind:value={allowText} rows="3" placeholder="go test"></textarea>
            </label>
            <label class="field">
              <span class="field-label">Max tokens per role</span>
              <input
                inputmode="numeric"
                bind:value={maxTokensText}
                placeholder="No limit"
              />
            </label>
            <label class="field">
              <span class="field-label">Max cost per role (USD)</span>
              <input inputmode="decimal" bind:value={maxCostText} placeholder="No limit" />
            </label>
          </div>
        {/if}
      </section>
    </div>

    <aside class="summary card elevated" aria-labelledby="summary-heading">
      <h2 id="summary-heading" class="title-large">Ready to add</h2>
      <dl>
        <div>
          <dt class="label-medium muted"><Icon name="folder" size={18} />Folder</dt>
          <dd class="title-small">{selected ? folderLabel : "None picked yet"}</dd>
        </div>
        {#if inspection}
          <div>
            <dt class="label-medium muted"><Icon name="git" size={18} />Git</dt>
            <dd class="title-small">
              {#if inspection.is_git}
                {inspection.branch ?? "unknown branch"},
                {inspection.dirty === null
                  ? "state unknown"
                  : inspection.dirty
                    ? "uncommitted changes"
                    : "no uncommitted changes"}
              {:else}
                Not a git repository
              {/if}
            </dd>
          </div>
          {#if inspection.languages.length > 0}
            <div>
              <dt class="label-medium muted"><Icon name="code" size={18} />Looks like</dt>
              <dd class="title-small">{inspection.languages.join(", ")}</dd>
            </div>
          {/if}
        {/if}
        <div>
          <dt class="label-medium muted"><Icon name="users" size={18} />Team</dt>
          <dd class="title-small">{chosenTemplate?.title ?? "Loading…"}</dd>
        </div>
        <div>
          <dt class="label-medium muted"><Icon name="shield" size={18} />Permissions</dt>
          <dd class="title-small">{MODES.find((entry) => entry.id === mode)?.label}</dd>
        </div>
      </dl>
      {#if startingPresets}
        <p class="body-small muted">Go and Rust commands will be switched on for this project.</p>
      {/if}
      {#if inspection && !inspection.is_git}
        <p class="body-small notice">
          Agents can't undo their edits here: this folder isn't a git repository.
        </p>
      {/if}
      <hr class="divider" />
      <p class="body-small muted">
        You'll write the first task on the next screen. The agent backend is the daemon's default
        unless you change it under Advanced.
      </p>
      {#if error}
        <p class="body-small error" role="alert">{error}</p>
      {/if}
      <div class="actions">
        <button type="submit" class="btn btn-filled" disabled={submitting || !selected}>
          {submitting ? "Adding…" : "Add project"}
        </button>
        <button type="button" class="btn btn-text" onclick={onCancel}>Cancel</button>
      </div>
    </aside>
  </div>

  <div class="bar">
    <span class="body-medium bar-text">
      {selected ? `Add ${projectName}` : "Pick a folder to continue"}
    </span>
    <button type="submit" class="btn btn-filled" disabled={submitting || !selected}>
      {submitting ? "Adding…" : "Add project"}
    </button>
  </div>
</form>

<style>
  .page {
    max-width: 72rem;
    margin: 0 auto;
    padding: 2rem 1.5rem 4rem;
    display: flex;
    flex-direction: column;
    gap: 1.75rem;
  }

  h1,
  h2,
  p {
    margin: 0;
  }

  header {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .layout {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 21rem;
    gap: 2rem;
    align-items: start;
  }

  .main {
    display: flex;
    flex-direction: column;
    gap: 1.75rem;
    min-width: 0;
  }

  section {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }

  .heading-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    flex-wrap: wrap;
  }

  .recent {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  .templates {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr));
    gap: 12px;
  }

  .template {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .template-head {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .template-text {
    color: inherit;
  }

  .roles {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  .template.selected .tag {
    background: var(--md-sys-color-surface-container-lowest);
    color: var(--md-sys-color-on-surface-variant);
  }

  .seg {
    align-self: flex-start;
  }

  .mode-text {
    max-width: 40rem;
  }

  .advanced-toggle {
    border: 0;
    min-height: 64px;
    text-align: left;
    color: var(--md-sys-color-on-surface);
  }

  .advanced-text {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-width: 0;
  }

  .advanced {
    margin-top: 0.75rem;
    padding: 1.5rem 1.25rem 1.25rem;
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1.25rem 1rem;
  }

  .advanced .wide {
    grid-column: 1 / -1;
  }

  .advanced :global(.field-label) {
    background: var(--md-sys-color-surface-container-low);
  }

  .summary {
    position: sticky;
    top: 1.5rem;
    padding: 24px;
    display: flex;
    flex-direction: column;
    gap: 1.25rem;
  }

  dl {
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }

  dt {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  dd {
    margin: 2px 0 0 24px;
  }

  .notice {
    padding: 10px 12px;
    border-radius: var(--md-sys-shape-corner-small);
    background: var(--md-sys-color-attention-container);
    color: var(--md-sys-color-on-attention-container);
  }

  .error {
    color: var(--md-sys-color-error);
  }

  .actions {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  /* On a narrow screen the summary sits below the form, so the primary action is pinned
   * instead (above the phone's navigation bar). Hidden on wide screens. */
  .bar {
    display: none;
  }

  @media (max-width: 640px) {
    .bar {
      --bar-bottom: 5.5rem;
    }
  }

  @media (max-width: 900px) {
    .bar {
      display: flex;
      align-items: center;
      gap: 12px;
      position: sticky;
      bottom: var(--bar-bottom, 1rem);
      z-index: 5;
      padding: 10px 16px;
      border-radius: var(--md-sys-shape-corner-large);
      background: var(--md-sys-color-inverse-surface);
      color: var(--md-sys-color-inverse-on-surface);
      box-shadow: var(--md-sys-elevation-level2);
    }

    .bar-text {
      flex: 1;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .bar :global(.btn-filled) {
      background: var(--md-sys-color-inverse-primary);
      color: var(--md-sys-color-inverse-surface);
    }

    .actions .btn-filled {
      display: none;
    }

    .layout {
      grid-template-columns: minmax(0, 1fr);
    }

    .summary {
      position: static;
    }

    .advanced {
      grid-template-columns: minmax(0, 1fr);
    }
  }
</style>
