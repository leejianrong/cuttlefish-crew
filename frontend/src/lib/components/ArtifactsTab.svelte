<script lang="ts">
  // The files an agent leaves in .cuttlefish/artifacts/ (CUT-97). Markdown, images, video and
  // HTML designs (CUT-175) are shown; other files are listed by name. The list is read again every few seconds while the tab is
  // open, so a file written now appears without a refresh.
  import type { Artifact, FleetClient } from "../api";
  import { EMPTY_TEXT, ago, isViewable, manifestFile, sameArtifacts, sizeText } from "../artifacts";
  import ArtifactView from "./ArtifactView.svelte";
  import PresentMode from "./PresentMode.svelte";

  let {
    client,
    projectId,
    active,
  }: { client: FleetClient; projectId: string; active: boolean } = $props();

  let files = $state<Artifact[]>([]);
  let loaded = $state(false);
  let failed = $state(false);
  let selected = $state<string | null>(null);
  let now = $state(Date.now() / 1000);
  let shownFor = "";
  let presenting = $state(false);

  const manifest = $derived(manifestFile(files));
  const current = $derived(files.find((f) => f.path === selected) ?? null);

  // Read the list while this tab is open. A different project starts from nothing.
  $effect(() => {
    const id = projectId;
    if (shownFor !== id) {
      files = [];
      loaded = false;
      selected = null;
      presenting = false;
      shownFor = id;
    }
    if (!active) return;
    const read = () =>
      client
        .listArtifacts(id)
        .then((next) => {
          if (id !== projectId) return;
          failed = false;
          loaded = true;
          now = Date.now() / 1000;
          if (!sameArtifacts(files, next)) files = next;
        })
        .catch(() => {
          if (id === projectId) failed = true;
        });
    void read();
    const timer = setInterval(read, 3000);
    return () => clearInterval(timer);
  });

  // With nothing chosen, show the newest file that can be shown.
  $effect(() => {
    const fallback = (files.find((f) => isViewable(f.kind)) ?? files[0])?.path ?? null;
    if (selected === null || !files.some((f) => f.path === selected)) selected = fallback;
  });

</script>

<section class="artifacts" aria-label="Artifacts">
  {#if failed && !loaded}
    <p class="body-medium muted">Couldn't read the artifacts folder.</p>
  {:else if !loaded}
    <p class="body-medium muted">Reading…</p>
  {:else if files.length === 0}
    <p class="body-medium muted empty">{EMPTY_TEXT}</p>
  {:else}
    {#if manifest}
      <button type="button" class="present" onclick={() => (presenting = true)}>Present</button>
    {/if}
    <div class="layout">
      <ul class="list" aria-label="Files">
        {#each files as file (file.path)}
          <li>
            <button
              class="file"
              class:current={file.path === selected}
              aria-current={file.path === selected ? "true" : undefined}
              onclick={() => (selected = file.path)}
            >
              <span class="name label-large">{file.path}</span>
              <span class="meta body-small muted">
                {isViewable(file.kind) ? file.kind : file.kind === "manifest" ? "walk-through" : "not shown"} · {sizeText(file.size)} · {ago(file.modified, now)}
              </span>
            </button>
          </li>
        {/each}
      </ul>
      <article class="view" aria-live="polite">
        {#if !current}
          <p class="muted body-medium">Pick a file.</p>
        {:else}
          <ArtifactView {client} {projectId} file={current} tall />
        {/if}
      </article>
    </div>
  {/if}
</section>

{#if presenting && manifest}
  <PresentMode {client} {projectId} {files} {manifest} onClose={() => (presenting = false)} />
{/if}

<style>
  .artifacts { padding: 0.5rem 0 1rem; }
  .empty { max-width: 40rem; }
  .present { margin-bottom: 0.75rem; min-height: 2.75rem; }
  .layout {
    display: grid;
    grid-template-columns: minmax(12rem, 18rem) minmax(0, 1fr);
    gap: 1.25rem;
    align-items: start;
  }
  .list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.25rem; }
  .file {
    width: 100%;
    text-align: left;
    display: flex;
    flex-direction: column;
    gap: 0.1rem;
    padding: 0.6rem 0.8rem;
    border-radius: var(--md-sys-shape-corner-medium);
    border: 1px solid transparent;
    background: transparent;
    color: var(--md-sys-color-on-surface);
    cursor: pointer;
  }
  .file.current {
    background: var(--md-sys-color-secondary-container);
    color: var(--md-sys-color-on-secondary-container);
  }
  .name { overflow-wrap: anywhere; }
  .view { min-width: 0; }
  @media (max-width: 720px) {
    .layout { grid-template-columns: minmax(0, 1fr); }
    .list { max-height: 14rem; overflow-y: auto; }
  }
</style>
