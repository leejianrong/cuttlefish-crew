<script lang="ts">
  import type {
    AccessLevel,
    BuiltinRole,
    FleetClient,
    ProjectSummary,
    RoleDefinition,
    TeamTemplate,
  } from "../api";
  import {
    addableBuiltins,
    builtinByName,
    canReset,
    isDefaultPrompt,
    removeRole,
    replaceRole,
    resetRole,
    roleFromBuiltin,
    roleNameProblem,
  } from "../team";
  import Icon from "./Icon.svelte";

  let {
    client,
    project,
    onChanged,
  }: { client: FleetClient; project: ProjectSummary; onChanged: () => Promise<void> } = $props();

  const BACKENDS = [
    { value: "", label: "Project default" },
    { value: "kopicode", label: "kopicode" },
    { value: "claude-code", label: "Claude Code" },
    { value: "codex", label: "Codex" },
  ];
  const ACCESS: { value: string; label: string }[] = [
    { value: "", label: "Project default" },
    { value: "ask-first", label: "Ask first" },
    { value: "standard", label: "Standard" },
    { value: "auto", label: "Auto" },
    { value: "read-only", label: "Read-only" },
  ];

  let builtins = $state<BuiltinRole[]>([]);
  let templates = $state<TeamTemplate[]>([]);
  let loadError = $state<string | null>(null);

  const firstRole = () => project.roles[0]?.name ?? null;
  let selectedName = $state<string | null>(firstRole());
  // The role being edited, copied so Save and Cancel mean something.
  let draft = $state<RoleDefinition | null>(null);
  let draftFor: string | null = null;
  let adding = $state(false);
  let newName = $state("");
  let confirmTemplate = $state<string | null>(null);
  let saving = $state(false);
  let error = $state<string | null>(null);
  let notice = $state<string | null>(null);

  const selected = $derived(project.roles.find((role) => role.name === selectedName) ?? null);
  const dirty = $derived(
    draft !== null &&
      selected !== null &&
      (draft.persona !== selected.persona ||
        (draft.backend ?? null) !== (selected.backend ?? null) ||
        (draft.access ?? null) !== (selected.access ?? null)),
  );
  const addable = $derived(addableBuiltins(project.roles, builtins));
  const newNameProblem = $derived(newName ? roleNameProblem(newName, project.roles) : null);

  $effect(() => {
    Promise.all([client.listBuiltinRoles(), client.listTemplates()]).then(
      ([roleResult, templateResult]) => {
        builtins = roleResult.roles;
        templates = templateResult.templates;
      },
      () => (loadError = "Couldn't load the built-in roles. Check that the daemon is still running."),
    );
  });

  // Start a fresh draft whenever a different role is selected.
  $effect(() => {
    if (selectedName !== draftFor) {
      draftFor = selectedName;
      draft = selected ? { ...selected } : null;
    }
  });

  function select(name: string) {
    selectedName = name;
    error = null;
    notice = null;
  }

  async function write(roles: RoleDefinition[], message: string) {
    saving = true;
    error = null;
    notice = null;
    try {
      await client.updateRoles(project.id, roles);
      await onChanged();
      notice = message;
      return true;
    } catch {
      error = "Couldn't save that. Check that the daemon is still running, then try again.";
      return false;
    } finally {
      saving = false;
    }
  }

  async function saveRole() {
    if (!draft || !selected) return;
    await write(replaceRole(project.roles, selected.name, draft), `Saved ${selected.name}.`);
  }

  function resetDraft() {
    if (draft) draft = resetRole(draft, builtins);
  }

  async function addBuiltin(name: string) {
    const builtin = builtinByName(builtins, name);
    if (!builtin) return;
    const ok = await write([...project.roles, roleFromBuiltin(builtin)], `Added ${name}.`);
    if (ok) {
      adding = false;
      selectedName = name;
    }
  }

  async function addCustom(event: SubmitEvent) {
    event.preventDefault();
    const name = newName.trim();
    if (roleNameProblem(name, project.roles) !== null) return;
    const ok = await write(
      [...project.roles, { name, persona: "", backend: null, access: null }],
      `Added ${name}.`,
    );
    if (ok) {
      newName = "";
      adding = false;
      selectedName = name;
    }
  }

  async function remove() {
    if (!selected) return;
    const name = selected.name;
    const remaining = removeRole(project.roles, name);
    const ok = await write(remaining, `Removed ${name}.`);
    if (ok) selectedName = remaining[0]?.name ?? null;
  }

  async function applyTemplate(name: string) {
    const template = templates.find((entry) => entry.name === name);
    if (!template) return;
    const roles = template.roles
      .map((roleName) => builtinByName(builtins, roleName))
      .filter((builtin): builtin is BuiltinRole => builtin !== undefined)
      .map(roleFromBuiltin);
    const ok = await write(roles, `Team replaced with ${template.title}.`);
    if (ok) {
      confirmTemplate = null;
      selectedName = roles[0]?.name ?? null;
      draftFor = null;
    }
  }

  function field(event: Event): string {
    return (event.currentTarget as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement)
      .value;
  }
</script>

<div class="team">
  {#if project.running}
    <p class="banner" role="status">
      A team is running. Changes apply the next time you start it, not to the one running now.
    </p>
  {/if}
  {#if loadError}
    <p class="error" role="alert">{loadError}</p>
  {/if}

  <section aria-labelledby="templates-heading">
    <h2 id="templates-heading" class="title-large">Start from a template</h2>
    <p class="body-medium muted">Replaces this project's roles, including any prompt you edited.</p>
    <div class="templates">
      {#each templates as template (template.name)}
        <div class="card filled template">
          <span class="title-medium">{template.title}</span>
          <span class="body-small muted">{template.summary}</span>
          <div class="tags">
            {#each template.roles as role (role)}<span class="tag">{role}</span>{/each}
          </div>
          {#if confirmTemplate === template.name}
            <div class="confirm" role="alert">
              <span class="body-small">Replace the current team?</span>
              <button class="btn btn-danger sm" disabled={saving} onclick={() => applyTemplate(template.name)}>
                Replace
              </button>
              <button class="btn btn-text sm" onclick={() => (confirmTemplate = null)}>Keep mine</button>
            </div>
          {:else}
            <button class="btn btn-outlined sm" onclick={() => (confirmTemplate = template.name)}>
              Use this team
            </button>
          {/if}
        </div>
      {/each}
    </div>
  </section>

  <section aria-labelledby="roles-heading">
    <div class="heading-row">
      <h2 id="roles-heading" class="title-large">Roles</h2>
      {#if notice}<span class="body-small muted" role="status">{notice}</span>{/if}
    </div>
    <div class="roles">
      <div class="list">
        {#each project.roles as role (role.name)}
          <button
            type="button"
            class="item role-item"
            class:selected={role.name === selectedName}
            aria-pressed={role.name === selectedName}
            onclick={() => select(role.name)}
          >
            <span class="role-text">
              <span class="title-medium">{role.name}</span>
              <span class="body-small">
                {builtinByName(builtins, role.name)?.summary ?? "Custom role"}
              </span>
            </span>
            {#if role.access === "read-only"}<span class="tag primary">Read-only</span>{/if}
          </button>
        {:else}
          <p class="body-medium muted">No roles yet. Add one below or use a template above.</p>
        {/each}

        {#if adding}
          <div class="add-panel card filled">
            {#if addable.length > 0}
              <span class="label-medium muted">Built-in roles</span>
              <div class="tags">
                {#each addable as builtin (builtin.name)}
                  <button class="chip" disabled={saving} onclick={() => addBuiltin(builtin.name)}>
                    <Icon name="plus" size={16} />{builtin.name}
                  </button>
                {/each}
              </div>
            {/if}
            <form class="custom" onsubmit={addCustom}>
              <label class="field">
                <span class="field-label">Or name a custom role</span>
                <input bind:value={newName} placeholder="poet" spellcheck="false" />
              </label>
              <button class="btn btn-tonal" type="submit" disabled={!newName.trim() || newNameProblem !== null || saving}>
                Add
              </button>
            </form>
            {#if newNameProblem === "duplicate"}
              <p class="body-small error">A role with that name is already on the team.</p>
            {/if}
            <button class="btn btn-text sm" onclick={() => (adding = false)}>Close</button>
          </div>
        {:else}
          <button class="btn btn-text has-icon" onclick={() => (adding = true)}>
            <Icon name="plus" size={18} />Add a role
          </button>
        {/if}
      </div>

      {#if draft && selected}
        <div class="card editor">
          <div class="editor-head">
            <h3 class="title-large">{selected.name}</h3>
            {#if isDefaultPrompt(draft, builtins)}
              <span class="tag primary">Default prompt</span>
            {:else if builtinByName(builtins, selected.name)}
              <span class="tag">Edited</span>
            {/if}
            <button
              class="btn btn-text sm reset"
              disabled={!canReset(draft, builtins)}
              onclick={resetDraft}
            >
              <Icon name="refresh" size={16} />Reset to default
            </button>
          </div>

          <div class="selects">
            <label class="field">
              <span class="field-label">Agent</span>
              <select
                value={draft.backend ?? ""}
                onchange={(event) => draft && (draft = { ...draft, backend: field(event) || null })}
              >
                {#each BACKENDS as option (option.value)}
                  <option value={option.value}>{option.label}</option>
                {/each}
              </select>
            </label>
            <label class="field">
              <span class="field-label">Permissions</span>
              <select
                value={draft.access ?? ""}
                onchange={(event) =>
                  draft && (draft = { ...draft, access: (field(event) || null) as AccessLevel | null })}
              >
                {#each ACCESS as option (option.value)}
                  <option value={option.value}>{option.label}</option>
                {/each}
              </select>
            </label>
          </div>
          {#if draft.access === "read-only"}
            <p class="body-small muted">
              Read-only limits the shell to looking around. It also stops edits on Claude Code and
              Codex, but not on kopicode.
            </p>
          {/if}

          <label class="field prompt">
            <span class="field-label">Prompt</span>
            <textarea
              rows="12"
              value={draft.persona}
              oninput={(event) => draft && (draft = { ...draft, persona: field(event) })}
            ></textarea>
          </label>

          {#if error}<p class="body-small error" role="alert">{error}</p>{/if}
          <div class="editor-actions">
            <button class="btn btn-danger-text" disabled={saving} onclick={remove}>
              <Icon name="trash" size={18} />Remove role
            </button>
            <span class="grow"></span>
            <button class="btn btn-text" disabled={!dirty || saving} onclick={() => (draft = selected ? { ...selected } : null)}>
              Discard changes
            </button>
            <button class="btn btn-filled" disabled={!dirty || saving} onclick={saveRole}>
              {saving ? "Saving…" : "Save role"}
            </button>
          </div>
          <p class="body-small muted">
            The agent also reads the project's own instructions file (CLAUDE.md or AGENTS.md) if it
            has one.
          </p>
        </div>
      {/if}
    </div>
  </section>
</div>

<style>
  .team {
    display: flex;
    flex-direction: column;
    gap: 1.75rem;
  }

  section {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }

  h2,
  h3,
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
  }

  .heading-row {
    display: flex;
    align-items: baseline;
    gap: 1rem;
  }

  .templates {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
    gap: 12px;
  }

  .template {
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    align-items: flex-start;
  }

  .tags {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  .confirm {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  .roles {
    display: grid;
    grid-template-columns: 20rem minmax(0, 1fr);
    gap: 1.5rem;
    align-items: start;
  }

  .list {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .role-item {
    min-height: 64px;
  }

  .role-text {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-width: 0;
  }

  .add-panel {
    padding: 14px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    align-items: flex-start;
  }

  .custom {
    display: flex;
    gap: 10px;
    align-items: center;
    width: 100%;
  }

  .custom .field {
    flex: 1;
    min-width: 0;
  }

  .add-panel :global(.field-label) {
    background: var(--md-sys-color-surface-container-low);
  }

  .editor {
    padding: 24px;
    display: flex;
    flex-direction: column;
    gap: 1.25rem;
  }

  .editor-head {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
  }

  .editor-head h3 {
    flex: 1;
  }

  .reset {
    gap: 6px;
  }

  .selects {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1rem;
  }

  .editor :global(.field-label) {
    background: var(--md-sys-color-surface-container-lowest);
  }

  .prompt textarea {
    line-height: 1.5rem;
    font-size: 0.875rem;
  }

  .editor-actions {
    display: flex;
    gap: 8px;
    align-items: center;
    flex-wrap: wrap;
  }

  .grow {
    flex: 1;
  }

  @media (max-width: 900px) {
    .roles {
      grid-template-columns: minmax(0, 1fr);
    }

    .selects {
      grid-template-columns: minmax(0, 1fr);
    }
  }
</style>
