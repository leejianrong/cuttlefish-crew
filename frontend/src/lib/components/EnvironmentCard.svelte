<script lang="ts">
  import type { FleetClient } from "../api";
  import { environmentRows, hasMissingInstall, type EnvironmentRow } from "../environment";

  let { client, projectId }: { client: FleetClient; projectId: string } = $props();

  // Read once when the card shows: a project's files change rarely, and a fresh start re-reads.
  let rows = $state<EnvironmentRow[] | null>(null);
  let missingRoot = $state<string | null>(null);
  let failed = $state(false);

  $effect(() => {
    const id = projectId;
    rows = null;
    missingRoot = null;
    failed = false;
    client
      .getEnvironment(id)
      .then((spec) => {
        if (id !== projectId) return;
        missingRoot = spec.root_exists ? null : spec.root;
        rows = environmentRows(spec);
      })
      .catch(() => {
        if (id === projectId) failed = true;
      });
  });
</script>

<section class="card filled env" aria-labelledby="env-heading">
  <h2 id="env-heading" class="title-medium">Environment</h2>
  <p class="body-small muted lede">What this project's files say it needs. Nothing is run to find out.</p>
  {#if failed}
    <p class="body-medium muted">Couldn't read the project's files.</p>
  {:else if rows === null}
    <p class="body-medium muted">Reading…</p>
  {:else if missingRoot}
    <p class="body-medium muted">
      This project's folder isn't there any more: <span class="mono">{missingRoot}</span>. It was
      moved or deleted; remove the project and add it again from its new place.
    </p>
  {:else if rows.length === 0}
    <p class="body-medium muted">
      No recognised project files at the top of this folder (Python, Node, Go, Rust, Java or
      Ruby). Files in subfolders are not scanned.
    </p>
  {:else}
    <ul class="rows">
      {#each rows as row (row.name)}
        <li>
          <span class="name title-small">{row.name}</span>
          <span class="details body-medium muted mono">{row.details.join(" · ")}</span>
          {#if row.stateLabel}
            <span class="state" class:ok={row.state === "installed"}>
              {row.state === "installed" ? "✓" : "–"} {row.stateLabel}
            </span>
          {/if}
        </li>
      {/each}
    </ul>
    {#if hasMissingInstall(rows)}
      <p class="body-small muted note">
        cuttlefish does not install dependencies yet, so agents start without them and tests that
        need them will fail. Install them in the project folder first.
      </p>
    {/if}
  {/if}
</section>

<style>
  .env {
    padding: 1.1rem 1.25rem;
    margin: 1rem 0;
    max-width: 56rem;
  }

  .lede {
    margin: 0.15rem 0 0.75rem;
  }

  .rows {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
  }

  li {
    display: grid;
    grid-template-columns: 5rem 1fr auto;
    gap: 0.25rem 0.75rem;
    align-items: baseline;
  }

  .details {
    overflow-wrap: anywhere;
    min-width: 0;
  }

  .state {
    color: var(--md-sys-color-on-surface-variant);
    font-size: 0.8rem;
    white-space: nowrap;
  }

  .state.ok {
    color: var(--md-sys-color-success);
  }

  .note {
    margin: 0.75rem 0 0;
  }

  /* On a phone the three columns do not fit: the state drops under the details. */
  @media (max-width: 480px) {
    li {
      grid-template-columns: 4.5rem 1fr;
    }

    .state {
      grid-column: 2;
      white-space: normal;
    }
  }
</style>
