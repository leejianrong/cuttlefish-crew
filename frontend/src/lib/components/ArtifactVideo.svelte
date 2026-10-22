<script lang="ts">
  // A video from the artifacts folder (CUT-175). Fetched with the token and played from a blob URL:
  // a `<video src>` cannot send the header. Held in memory, which is why the daemon caps it.
  import type { FleetClient } from "../api";

  let {
    client,
    projectId,
    path,
    label,
  }: { client: FleetClient; projectId: string; path: string; label: string } = $props();

  let url = $state<string | null>(null);
  let failed = $state(false);

  $effect(() => {
    const wanted = path;
    let made: string | null = null;
    let cancelled = false;
    url = null;
    failed = false;
    client
      .readArtifact(projectId, wanted)
      .then((blob) => {
        if (cancelled) return;
        made = URL.createObjectURL(blob);
        url = made;
      })
      .catch(() => {
        if (!cancelled) failed = true;
      });
    return () => {
      cancelled = true;
      if (made) URL.revokeObjectURL(made);
    };
  });
</script>

{#if url}
  <!-- svelte-ignore a11y_media_has_caption -->
  <video src={url} controls preload="metadata" aria-label={label}></video>
{:else if failed}
  <span class="muted body-small">{label} (video not available)</span>
{:else}
  <span class="muted body-small">Loading video…</span>
{/if}

<style>
  video {
    max-width: 100%;
    max-height: 60vh;
    border-radius: var(--md-sys-shape-corner-medium, 12px);
    background: var(--md-sys-color-surface-container-highest);
  }
</style>
