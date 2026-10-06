<script lang="ts">
  import Icon from "./Icon.svelte";
  import type { RoleStatus } from "../api";
  import RoleSprite from "./RoleSprite.svelte";

  let { onBack }: { onBack: () => void } = $props();

  const STATUSES: { status: RoleStatus; blurb: string }[] = [
    { status: "queued", blurb: "declared, hasn't started its first round yet" },
    { status: "working", blurb: "a round is in flight" },
    { status: "blocked", blurb: "the backend refused and needs a human" },
    { status: "done", blurb: "reached TaskCompleted" },
    { status: "failed", blurb: "reached TaskFailed" },
    { status: "stopped", blurb: "the operator stopped the team before this role finished" },
  ];
</script>

<div class="page">
  <button class="btn btn-text back" onclick={onBack}><Icon name="back" size={18} />Back</button>
  <h1>Sprite gallery</h1>
  <p class="hint">
    Every role status this app can show, live-rendered, no daemon connection needed. This
    is what a role's own cuttlefish looks like at each point in `cuttlefish.fleet.status
    .RoleStatus`.
  </p>

  <div class="grid">
    {#each STATUSES as entry (entry.status)}
      <div class="cell">
        <div class="stage">
          <RoleSprite status={entry.status} size={7} />
        </div>
        <span class="label mono">{entry.status}</span>
        <p class="blurb">{entry.blurb}</p>
      </div>
    {/each}
  </div>
</div>

<style>
  .page {
    max-width: 48rem;
    margin: 0 auto;
    padding: 2rem 1.5rem 4rem;
  }

  .back {
    margin: 0 0 0.75rem -12px;
    padding: 0 16px 0 12px;
  }

  h1 {
    font-size: 1.4rem;
    margin: 0 0 0.5rem;
  }

  .hint {
    color: var(--text-muted);
    font-size: 0.85rem;
    line-height: 1.6;
    max-width: 38rem;
    margin: 0 0 2rem;
  }

  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr));
    gap: 1rem;
  }

  .cell {
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    border-radius: var(--md-sys-shape-corner-medium);
    padding: 1.25rem 1rem;
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
    gap: 0.5rem;
  }

  .stage {
    height: 4.5rem;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .label {
    font-weight: 700;
    font-size: 0.85rem;
  }

  .blurb {
    color: var(--text-faint);
    font-size: 0.76rem;
    margin: 0;
    line-height: 1.4;
  }
</style>
