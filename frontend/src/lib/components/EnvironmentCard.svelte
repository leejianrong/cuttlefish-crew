<script lang="ts">
  import type { FleetClient, PrepareSetting } from "../api";
  import {
    PREPARE_SETTINGS,
    environmentRows,
    hasMissingInstall,
    missingInstallNote,
    type EnvironmentRow,
  } from "../environment";
  import { radiogroup } from "../radiogroup";

  let {
    client,
    projectId,
    refreshKey = "",
    onSettingChanged,
  }: {
    client: FleetClient;
    projectId: string;
    /** Changes when the project's files may have changed under the card (an install ran, the
     * setting moved): the card reads again. */
    refreshKey?: string;
    onSettingChanged?: () => void;
  } = $props();

  // Read once when the card shows: a project's files change rarely, and a fresh start re-reads.
  let rows = $state<EnvironmentRow[] | null>(null);
  let missingRoot = $state<string | null>(null);
  let unsupported = $state<{ name: string; reason: string }[]>([]);
  let setting = $state<PrepareSetting>("ask");
  let saving = $state(false);
  let saveError = $state<string | null>(null);
  let failed = $state(false);

  // A re-read of the same project keeps what is on screen until the answer arrives; another
  // project starts from "Reading…" so one project's files are never shown under another's name.
  let shownFor = "";

  $effect(() => {
    const id = projectId;
    void refreshKey;
    if (shownFor !== id) {
      rows = null;
      missingRoot = null;
      shownFor = id;
    }
    failed = false;
    client
      .getEnvironment(id)
      .then((spec) => {
        if (id !== projectId) return;
        missingRoot = spec.root_exists ? null : spec.root;
        unsupported = spec.prepare.unsupported;
        setting = spec.prepare.setting;
        rows = environmentRows(spec);
      })
      .catch(() => {
        if (id === projectId) failed = true;
      });
  });

  async function choose(next: PrepareSetting) {
    if (next === setting || saving) return;
    saving = true;
    saveError = null;
    try {
      const updated = await client.updateEnvironmentPrepare(projectId, next);
      setting = updated.env_prepare;
      onSettingChanged?.();
    } catch {
      saveError = "Couldn't save that. Check that the daemon is still running.";
    } finally {
      saving = false;
    }
  }

  const settingText = $derived(PREPARE_SETTINGS.find((entry) => entry.id === setting)?.text ?? "");
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
      No recognised project files in this folder or the folders directly under it (Python, Node,
      Go, Rust, Java or Ruby). Anything deeper is not scanned.
    </p>
  {:else}
    <ul class="rows">
      {#each rows as row (row.key)}
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
    {#if unsupported.length > 0}
      <p class="body-small muted note">
        cuttlefish does not install {unsupported.map((entry) => entry.name).join(", ")} dependencies
        yet.
      </p>
    {/if}
    {#if hasMissingInstall(rows)}
      <p class="body-small muted note">{missingInstallNote(setting)}</p>
    {/if}
  {/if}

  {#if !failed && rows !== null && !missingRoot && rows.length > 0}
    <div class="setting">
      <h3 id="prepare-setting" class="title-small">Install dependencies before a team starts</h3>
      <div class="seg" role="radiogroup" aria-labelledby="prepare-setting" use:radiogroup>
        {#each PREPARE_SETTINGS as entry (entry.id)}
          <button
            type="button"
            role="radio"
            aria-checked={entry.id === setting}
            tabindex={entry.id === setting ? 0 : -1}
            class:selected={entry.id === setting}
            aria-disabled={saving}
            onclick={() => choose(entry.id)}
          >
            {entry.label}
          </button>
        {/each}
      </div>
      <p class="body-small muted setting-text">{settingText}</p>
      {#if saveError}<p class="body-small error" role="alert">{saveError}</p>{/if}
    </div>
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

  .setting {
    margin-top: 1rem;
    padding-top: 0.9rem;
    border-top: 1px solid var(--md-sys-color-outline-variant);
  }

  .setting h3 {
    margin: 0 0 0.5rem;
  }

  .setting-text {
    margin: 0.5rem 0 0;
    max-width: 40rem;
  }

  .error {
    color: var(--md-sys-color-error);
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
