<script lang="ts">
  import { FleetApiError, type FleetClient, type FolderListing } from "../api";
  import { breadcrumbs } from "../folders";
  import Icon from "./Icon.svelte";

  let {
    client,
    selected,
    onSelect,
    browsePath = $bindable(),
  }: {
    client: FleetClient;
    /** The chosen folder, or null before one is picked. */
    selected: string | null;
    onSelect: (path: string) => void;
    /** The folder being shown; set it from outside to jump the list somewhere. */
    browsePath?: string | undefined;
  } = $props();

  let listing = $state<FolderListing | null>(null);
  let loading = $state(true);
  let error = $state<string | null>(null);
  let typed = $state("");
  let latest = 0;

  async function load(path: string | undefined) {
    const ticket = ++latest;
    loading = true;
    try {
      const result = await client.listFolders(path);
      if (ticket !== latest) return;
      listing = result;
      error = null;
      typed = result.path;
    } catch (caught) {
      if (ticket !== latest) return;
      error =
        caught instanceof FleetApiError && caught.status === 403
          ? "That folder is outside the ones this daemon can browse. Start it with --browse-root to add one."
          : caught instanceof FleetApiError && caught.status === 404
            ? "No folder at that path."
            : "Couldn't read that folder. Check that the daemon is still running.";
    } finally {
      if (ticket === latest) loading = false;
    }
  }

  $effect(() => {
    load(browsePath);
  });

  const crumbs = $derived(listing ? breadcrumbs(listing.path, listing.root) : []);

  function open(path: string) {
    browsePath = path;
  }

  function pickTyped(event: SubmitEvent) {
    event.preventDefault();
    const path = typed.trim();
    if (!path) return;
    browsePath = path;
    onSelect(path);
  }
</script>

<div class="picker card">
  <div class="bar">
    <button
      type="button"
      class="btn btn-text icon-btn"
      aria-label="Up one folder"
      disabled={!listing || listing.parent === null}
      onclick={() => listing?.parent && open(listing.parent)}
    >
      <Icon name="arrow-up" />
    </button>
    <nav class="crumbs" aria-label="Folder path">
      {#each crumbs as crumb, index (crumb.path)}
        {#if index > 0}<Icon name="chevron-right" size={18} />{/if}
        <button
          type="button"
          class="chip"
          class:selected={index === crumbs.length - 1}
          aria-current={index === crumbs.length - 1 ? "location" : undefined}
          onclick={() => open(crumb.path)}
        >
          {crumb.label}
        </button>
      {/each}
    </nav>
  </div>

  <div class="list" aria-busy={loading}>
    {#if error}
      <p class="message error" role="alert">{error}</p>
    {:else if listing && listing.folders.length === 0}
      <p class="message">No subfolders here. Pick this folder with the path box below.</p>
    {/if}
    {#each listing?.folders ?? [] as folder (folder.path)}
      <div class="row">
        <button
          type="button"
          class="item select"
          class:selected={folder.path === selected}
          aria-pressed={folder.path === selected}
          onclick={() => onSelect(folder.path)}
          ondblclick={() => open(folder.path)}
        >
          <Icon name="folder" />
          <span class="name">{folder.name}</span>
          {#if folder.is_git}
            <span class="tag"><Icon name="git" size={14} />git</span>
          {/if}
          {#if folder.path === selected}<Icon name="check" />{/if}
        </button>
        <button
          type="button"
          class="btn btn-text icon-btn"
          aria-label="Open {folder.name}"
          onclick={() => open(folder.path)}
        >
          <Icon name="chevron-right" />
        </button>
      </div>
    {/each}
    {#if listing?.truncated}
      <p class="message">Only the first folders are shown. Type a path below to go further.</p>
    {/if}
  </div>

  <form class="path" onsubmit={pickTyped}>
    <label class="field">
      <span class="field-label">Folder path</span>
      <input class="mono" bind:value={typed} spellcheck="false" autocomplete="off" />
    </label>
    <button type="submit" class="btn btn-tonal">Use this path</button>
  </form>
</div>

<style>
  .picker {
    overflow: hidden;
  }

  .bar {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 8px 12px;
    background: var(--md-sys-color-surface-container-low);
    border-bottom: 1px solid var(--md-sys-color-outline-variant);
  }

  .crumbs {
    display: flex;
    align-items: center;
    gap: 4px;
    flex-wrap: wrap;
    min-width: 0;
    color: var(--md-sys-color-on-surface-variant);
  }

  .list {
    padding: 8px;
    max-height: 22rem;
    overflow-y: auto;
  }

  .row {
    display: flex;
    align-items: center;
    gap: 2px;
  }

  .select {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 12px;
    min-height: 48px;
    padding: 4px 12px;
    border: 0;
    border-radius: var(--md-sys-shape-corner-medium);
    background: transparent;
    color: var(--md-sys-color-on-surface);
    text-align: left;
  }

  .select.selected {
    background: var(--md-sys-color-secondary-container);
    color: var(--md-sys-color-on-secondary-container);
  }

  .name {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font: 500 1rem/1.5rem var(--md-ref-typeface-plain);
  }

  .message {
    margin: 0.75rem 1rem;
    color: var(--md-sys-color-on-surface-variant);
    font-size: 0.875rem;
  }

  .message.error {
    color: var(--md-sys-color-error);
  }

  .path {
    display: flex;
    gap: 12px;
    align-items: center;
    padding: 20px 12px 12px;
    border-top: 1px solid var(--md-sys-color-outline-variant);
  }

  .path .field {
    flex: 1;
    min-width: 0;
  }

  .path :global(.field-label) {
    background: var(--md-sys-color-surface-container-lowest);
  }
</style>
