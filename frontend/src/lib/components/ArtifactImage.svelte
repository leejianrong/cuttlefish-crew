<script lang="ts">
  // An image from the artifacts folder. It is fetched with the token (an <img src> cannot send
  // one) and shown from a blob URL, as an image only: an SVG's scripts never run that way.
  import type { FleetClient } from "../api";

  let {
    client,
    projectId,
    path,
    alt,
  }: { client: FleetClient; projectId: string; path: string; alt: string } = $props();

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
  <img src={url} {alt} />
{:else if failed}
  <span class="muted body-small">{alt || path} (image not available)</span>
{:else}
  <span class="muted body-small">Loading image…</span>
{/if}

<style>
  img {
    max-width: 100%;
    height: auto;
    border-radius: var(--md-sys-shape-corner-small);
  }
</style>
