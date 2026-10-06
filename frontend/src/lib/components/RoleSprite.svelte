<script lang="ts">
  import type { RoleStatus } from "../api";
  import { CUTTLEFISH_FRAMES, STATUS_GLYPH, paletteFor } from "../pixel/cuttlefish";
  import PixelGrid from "../pixel/PixelGrid.svelte";

  let { status, size = 4 }: { status: RoleStatus; size?: number } = $props();

  let frameKey = $state<"a" | "b">("a");

  $effect(() => {
    if (status !== "working") {
      frameKey = "a";
      return;
    }
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      return;
    }
    const interval = setInterval(() => {
      frameKey = frameKey === "a" ? "b" : "a";
    }, 450);
    return () => clearInterval(interval);
  });

  const frame = $derived(CUTTLEFISH_FRAMES[frameKey]);
  const palette = $derived(paletteFor(status));
  const glyph = $derived(STATUS_GLYPH[status]);
</script>

<div class="sprite" class:working={status === "working"} role="img" aria-label="{status}">
  {#if glyph}
    <span class="glyph glyph-{status}">{glyph}</span>
  {/if}
  <PixelGrid {frame} {palette} pixelSize={size} />
</div>

<style>
  .sprite {
    position: relative;
    display: inline-block;
    line-height: 0;
  }

  .sprite.working {
    animation: bob 0.9s ease-in-out infinite;
  }

  .glyph {
    position: absolute;
    top: -0.75em;
    right: -0.35em;
    font-family: var(--font-mono);
    font-weight: 700;
    font-size: 0.85rem;
    line-height: 1;
  }

  .glyph-queued {
    color: var(--status-queued-fg);
  }
  .glyph-blocked {
    color: var(--status-blocked-fg);
  }
  .glyph-stopped {
    color: var(--status-queued-fg);
  }
  .glyph-done {
    color: var(--status-done-fg);
  }
  .glyph-failed {
    color: var(--status-failed-fg);
  }

  @keyframes bob {
    0%,
    100% {
      transform: translateY(0);
    }
    50% {
      transform: translateY(-2px);
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .sprite.working {
      animation: none;
    }
  }
</style>
