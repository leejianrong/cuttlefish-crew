<script lang="ts">
  import type { PixelFrame } from "./cuttlefish";
  import { pixelImage } from "./pixelimage";

  let {
    frame,
    palette,
    pixelSize = 3,
  }: { frame: PixelFrame; palette: Record<string, string>; pixelSize?: number } = $props();

  const cols = $derived(frame[0]?.length ?? 0);
  const rows = $derived(frame.length);
</script>

<!-- One element per sprite: a card draws two, and a fleet of 100 projects drew 20,000 nodes when
     each pixel was its own span (CUT-192). -->
<div
  class="pixel-grid"
  style:width="{cols * pixelSize}px"
  style:height="{rows * pixelSize}px"
  style:background-image={pixelImage(frame, palette)}
  aria-hidden="true"
></div>

<style>
  .pixel-grid {
    background-size: 100% 100%;
    background-repeat: no-repeat;
    image-rendering: pixelated;
  }
</style>
