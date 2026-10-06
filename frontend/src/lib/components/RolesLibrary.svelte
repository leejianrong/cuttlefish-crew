<script lang="ts">
  import type { BuiltinRole, FleetClient, TeamTemplate } from "../api";

  let { client }: { client: FleetClient } = $props();

  let roles = $state<BuiltinRole[]>([]);
  let templates = $state<TeamTemplate[]>([]);
  let selectedName = $state<string | null>(null);
  let error = $state<string | null>(null);

  const selected = $derived(roles.find((role) => role.name === selectedName) ?? roles[0] ?? null);

  $effect(() => {
    Promise.all([client.listBuiltinRoles(), client.listTemplates()]).then(
      ([roleResult, templateResult]) => {
        roles = roleResult.roles;
        templates = templateResult.templates;
      },
      () => (error = "Couldn't load the built-in roles. Check that the daemon is still running."),
    );
  });
</script>

<div class="page">
  <header>
    <h1 class="headline-medium">Roles and teams</h1>
    <p class="body-large muted">
      The ready-made roles and teams a new project starts from. To change the roles a project
      actually uses, open the project and go to its Team tab.
    </p>
  </header>

  {#if error}<p class="error" role="alert">{error}</p>{/if}

  <section aria-labelledby="templates-heading">
    <h2 id="templates-heading" class="title-large">Team templates</h2>
    <div class="templates">
      {#each templates as template (template.name)}
        <div class="card filled template">
          <span class="title-medium">{template.title}</span>
          <span class="body-small muted">{template.summary}</span>
          <div class="tags">
            {#each template.roles as role (role)}<span class="tag">{role}</span>{/each}
          </div>
        </div>
      {/each}
    </div>
  </section>

  <section aria-labelledby="roles-heading">
    <h2 id="roles-heading" class="title-large">Roles</h2>
    <div class="roles">
      <div class="list">
        {#each roles as role (role.name)}
          <button
            type="button"
            class="item role-item"
            class:selected={role.name === selected?.name}
            aria-pressed={role.name === selected?.name}
            onclick={() => (selectedName = role.name)}
          >
            <span class="role-text">
              <span class="title-medium">{role.name}</span>
              <span class="body-small">{role.summary}</span>
            </span>
            {#if role.access === "read-only"}<span class="tag primary">Read-only</span>{/if}
          </button>
        {/each}
      </div>
      {#if selected}
        <div class="card detail">
          <div class="detail-head">
            <h3 class="title-large">{selected.name}</h3>
            <span class="tag" class:primary={selected.access === "read-only"}>
              {selected.access === "read-only" ? "Read-only" : "Uses the project's mode"}
            </span>
          </div>
          <p class="prompt">{selected.prompt}</p>
          <p class="body-small muted">
            This is the prompt a project gets when it adds {selected.name}. Each project keeps its
            own copy, so editing it there never changes this one.
          </p>
        </div>
      {/if}
    </div>
  </section>
</div>

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
  h3,
  p {
    margin: 0;
  }

  header {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  section {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }

  .error {
    color: var(--md-sys-color-error);
  }

  .templates {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr));
    gap: 12px;
  }

  .template {
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .tags {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
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

  .detail {
    padding: 24px;
    display: flex;
    flex-direction: column;
    gap: 1rem;
  }

  .detail-head {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .detail-head h3 {
    flex: 1;
  }

  .prompt {
    white-space: pre-wrap;
    font: 400 0.875rem/1.5rem var(--md-ref-typeface-plain);
    padding: 16px;
    border-radius: var(--md-sys-shape-corner-medium);
    background: var(--md-sys-color-surface-container);
  }

  @media (max-width: 900px) {
    .roles {
      grid-template-columns: minmax(0, 1fr);
    }
  }
</style>
