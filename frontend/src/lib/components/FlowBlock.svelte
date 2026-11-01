<script lang="ts">
  // A ```mermaid flowchart, drawn as inline SVG from the parsed subset (CUT-278). Colours are the
  // theme's roles, so it follows light and dark; it never grows past its natural size and scales
  // down to fit a phone.
  import { describeFlow, layoutFlow, type FlowSpec } from "../flow";

  let { spec }: { spec: FlowSpec } = $props();

  const layout = $derived(layoutFlow(spec));
  const summary = $derived(describeFlow(spec));
</script>

<figure class="flow">
  <svg viewBox="0 0 {layout.width} {layout.height}" style:max-width="{layout.width}px" role="img" aria-label={summary}>
    <defs>
      <marker id="flow-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerUnits="userSpaceOnUse" markerWidth="9" markerHeight="9" orient="auto-start-reverse">
        <path d="M0 0 L10 5 L0 10 z" class="head" />
      </marker>
    </defs>
    {#each layout.edges as e, k (k)}
      <line
        class="edge {e.style}"
        x1={e.x1}
        y1={e.y1}
        x2={e.x2}
        y2={e.y2}
        marker-end={e.style === "plain" ? undefined : "url(#flow-arrow)"}
      />
      {#if e.label}
        <text class="label" x={(e.x1 + e.x2) / 2} y={(e.y1 + e.y2) / 2 - 4} text-anchor="middle">{e.label}</text>
      {/if}
    {/each}
    {#each layout.nodes as n (n.id)}
      {#if n.shape === "diamond"}
        <polygon class="node" points="{n.x},{n.y - n.h / 2} {n.x + n.w / 2},{n.y} {n.x},{n.y + n.h / 2} {n.x - n.w / 2},{n.y}" />
      {:else}
        <rect class="node" x={n.x - n.w / 2} y={n.y - n.h / 2} width={n.w} height={n.h} rx={n.shape === "round" ? n.h / 2 : 6} />
      {/if}
      <text class="text" x={n.x} y={n.y + 4.5} text-anchor="middle">{n.text}</text>
    {/each}
  </svg>
</figure>

<style>
  .flow { margin: 0.75rem 0; }
  svg { width: 100%; height: auto; display: block; }
  .node { fill: var(--md-sys-color-secondary-container); stroke: var(--md-sys-color-outline); stroke-width: 1.25; }
  .text { fill: var(--md-sys-color-on-secondary-container); font-size: 13px; }
  .edge { stroke: var(--md-sys-color-on-surface-variant); stroke-width: 1.5; }
  .edge.thick { stroke-width: 3; }
  .edge.dotted { stroke-dasharray: 4 4; }
  .head { fill: var(--md-sys-color-on-surface-variant); }
  .label { fill: var(--md-sys-color-on-surface-variant); font-size: 12px; paint-order: stroke; stroke: var(--md-sys-color-surface); stroke-width: 4px; }
</style>
