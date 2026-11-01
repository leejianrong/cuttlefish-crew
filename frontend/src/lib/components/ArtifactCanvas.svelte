<script lang="ts">
  // The Chat tab's right panel (CUT-175, docs/design/chat-rework/Main.dc.html): what the builder
  // leaves in .cuttlefish/artifacts/, newest first, one at a time. Images, video and HTML designs
  // here; the Artifacts tab keeps the full list. It follows the newest file until the person picks
  // another, so a chart the builder just drew appears without a click.
  import type { Artifact, FleetClient } from "../api";
  import { ago, canvasFiles, manifestFile, sameArtifacts } from "../artifacts";
  import ArtifactView from "./ArtifactView.svelte";
  import PresentMode from "./PresentMode.svelte";

  let {
    client,
    projectId,
    active,
    pinned: wanted = null,
    onOpenAll,
  }: {
    client: FleetClient;
    projectId: string;
    active: boolean;
    /** A file the thread asked to be shown (CUT-179). */
    pinned?: string | null;
    onOpenAll: () => void;
  } = $props();

  let files = $state<Artifact[]>([]);
  let pinned = $state<string | null>(null);
  let now = $state(Date.now() / 1000);
  let shownFor = "";
  let presenting = $state(false);

  const manifest = $derived(manifestFile(files));
  const shown = $derived(canvasFiles(files));
  const current = $derived(shown.find((f) => f.path === pinned) ?? shown[0] ?? null);

  $effect(() => {
    if (wanted) pinned = wanted;
  });

  $effect(() => {
    const id = projectId;
    if (shownFor !== id) {
      files = [];
      pinned = null;
      presenting = false;
      shownFor = id;
    }
    if (!active) return;
    let stop = false;
    const read = async () => {
      try {
        const next = await client.listArtifacts(id);
        if (stop || id !== projectId) return;
        now = Date.now() / 1000;
        if (!sameArtifacts(files, next)) files = next;
      } catch {
        // The thread's own notice covers a daemon that cannot be reached.
      }
    };
    void read();
    const timer = setInterval(read, 3000);
    return () => {
      stop = true;
      clearInterval(timer);
    };
  });
</script>

<aside class="canvas" aria-label="What the builder made">
  {#if shown.length === 0}
    <p class="body-small muted">
      Nothing to show yet. When the builder writes an image, a video or an HTML design to
      <span class="mono">.cuttlefish/artifacts/</span>, it appears here.
    </p>
  {:else}
    <div class="chips" role="group" aria-label="Files">
      {#each shown as file (file.path)}
        <button
          type="button"
          class="chip"
          class:selected={file.path === current?.path}
          aria-pressed={file.path === current?.path}
          onclick={() => (pinned = file.path)}
        >
          {file.name}
        </button>
      {/each}
    </div>
    {#if current}
      <div class="stage" aria-live="polite">
        <p class="label-small muted">{current.kind} · {ago(current.modified, now)}</p>
        <ArtifactView {client} {projectId} file={current} />
      </div>
    {/if}
  {/if}
  {#if manifest}
    <button type="button" class="link" onclick={() => (presenting = true)}>Present</button>
  {/if}
  <button type="button" class="link" onclick={onOpenAll}>All artifacts</button>
</aside>

{#if presenting && manifest}
  <PresentMode {client} {projectId} {files} {manifest} onClose={() => (presenting = false)} />
{/if}

<style>
  .canvas {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    min-width: 0;
  }

  p {
    margin: 0;
  }

  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
  }

  .chips :global(.chip) {
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .stage {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    min-width: 0;
  }

  .link {
    align-self: flex-start;
    padding: 0;
    border: 0;
    background: none;
    color: var(--md-sys-color-primary);
    font: inherit;
    text-decoration: underline;
    cursor: pointer;
    min-height: 2.75rem;
  }
</style>
