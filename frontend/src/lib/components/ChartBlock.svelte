<script lang="ts">
  // A ```chart block, drawn as inline SVG from numbers only (CUT-114). Colours are the theme's
  // roles, so it follows light and dark; the SVG scales to its container, so it fits a phone.
  import { CHART_H, CHART_W, describeChart, layoutChart, type ChartSpec } from "../chart";

  let { spec }: { spec: ChartSpec } = $props();

  const layout = $derived(layoutChart(spec));
  const summary = $derived(describeChart(spec));
  const named = $derived(spec.series.some((s) => s.name));
</script>

<figure class="chart">
  {#if spec.title}<figcaption class="title-small">{spec.title}</figcaption>{/if}
  <svg viewBox="0 0 {CHART_W} {CHART_H}" role="img" aria-label={summary}>
    {#each layout.ticks as tick (tick.value)}
      <line class="grid" x1={layout.plot.left} x2={layout.plot.right} y1={tick.y} y2={tick.y} />
      <text class="tick" x={layout.plot.left - 6} y={tick.y + 4} text-anchor="end">{tick.value}</text>
    {/each}
    <line class="axis" x1={layout.plot.left} x2={layout.plot.right} y1={layout.zeroY} y2={layout.zeroY} />
    {#each layout.bars as bar, k (k)}
      <rect class="s{bar.series}" x={bar.x} y={bar.y} width={bar.w} height={bar.h} rx="2" />
    {/each}
    {#each layout.lines as line (line.series)}
      <polyline class="line s{line.series}" points={line.points.map((p) => `${p.x},${p.y}`).join(" ")} />
      {#each line.points as p, k (k)}
        <circle class="dot s{line.series}" cx={p.x} cy={p.y} r="3.5" />
      {/each}
    {/each}
    {#each layout.labels as label (label.x)}
      <text class="tick" x={label.x} y={layout.plot.bottom + 18} text-anchor="middle">{label.text}</text>
    {/each}
  </svg>
  {#if named}
    <ul class="legend body-small">
      {#each spec.series as s, k (k)}
        <li><span class="swatch s{k}" aria-hidden="true"></span>{s.name}</li>
      {/each}
    </ul>
  {/if}
</figure>

<style>
  .chart { margin: 0.75rem 0; max-width: 40rem; }
  figcaption { margin-bottom: 0.25rem; }
  svg { width: 100%; height: auto; display: block; }
  .grid { stroke: var(--md-sys-color-outline-variant); stroke-width: 1; }
  .axis { stroke: var(--md-sys-color-outline); stroke-width: 1.5; }
  .tick { fill: var(--md-sys-color-on-surface-variant); font-size: 12px; }
  rect.s0, .swatch.s0 { fill: var(--md-sys-color-primary); background: var(--md-sys-color-primary); }
  rect.s1, .swatch.s1 { fill: var(--md-sys-color-tertiary); background: var(--md-sys-color-tertiary); }
  rect.s2, .swatch.s2 { fill: var(--md-sys-color-secondary); background: var(--md-sys-color-secondary); }
  rect.s3, .swatch.s3 { fill: var(--md-sys-color-error); background: var(--md-sys-color-error); }
  rect.s4, .swatch.s4 { fill: var(--md-sys-color-outline); background: var(--md-sys-color-outline); }
  rect.s5, .swatch.s5 { fill: var(--md-sys-color-on-surface-variant); background: var(--md-sys-color-on-surface-variant); }
  .line { fill: none; stroke-width: 2.5; stroke-linejoin: round; }
  .line.s0, .dot.s0 { stroke: var(--md-sys-color-primary); }
  .line.s1, .dot.s1 { stroke: var(--md-sys-color-tertiary); }
  .line.s2, .dot.s2 { stroke: var(--md-sys-color-secondary); }
  .line.s3, .dot.s3 { stroke: var(--md-sys-color-error); }
  .line.s4, .dot.s4 { stroke: var(--md-sys-color-outline); }
  .line.s5, .dot.s5 { stroke: var(--md-sys-color-on-surface-variant); }
  .dot { fill: var(--md-sys-color-surface); stroke-width: 2.5; }
  .legend { list-style: none; display: flex; flex-wrap: wrap; gap: 0.25rem 1rem; padding: 0; margin: 0.25rem 0 0; }
  .swatch { display: inline-block; width: 0.8rem; height: 0.8rem; border-radius: 2px; margin-right: 0.35rem; }
</style>
