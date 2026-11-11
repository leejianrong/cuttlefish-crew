<script lang="ts">
  import type { FleetClient, WorkerInfo } from "../api";
  import { windowText, warningLines, type BackendUsage, type UsageView } from "../usage";

  let { client, workers = [] }: { client: FleetClient; workers?: WorkerInfo[] } = $props();

  let view = $state<UsageView | null>(null);
  let now = $state(Date.now() / 1000);

  $effect(() => {
    const load = () =>
      client
        .getUsage()
        .then((result) => (view = result))
        .catch(() => (view = null));
    load();
    const poll = setInterval(load, 5000);
    const tick = setInterval(() => (now = Date.now() / 1000), 5000);
    return () => {
      clearInterval(poll);
      clearInterval(tick);
    };
  });

  // This PC first, then each worker that has reported anything.
  const places = $derived<{ name: string; usage: Record<string, BackendUsage> }[]>([
    ...(view ? [{ name: "this PC", usage: view.local }] : []),
    ...workers.filter((w) => w.online && w.usage).map((w) => ({ name: w.name, usage: w.usage! })),
  ]);
  const warnings = $derived(places.flatMap((place) => warningLines(place.usage, place.name, now)));
</script>

{#if view}
  <section class="card">
    <h2 class="title-medium">Agent usage</h2>
    <p class="muted">
      What each tool says about its own limits, read from what it already reports. A tool that says
      nothing is listed as not reported. You are warned at {view.warn_at_percent}%.
    </p>
    {#each warnings as line (line)}
      <p class="flag" role="status">{line}</p>
    {/each}
    {#each places as place (place.name)}
      <div class="place">
        <p class="name">{place.name}</p>
        {#each Object.entries(place.usage) as [backend, entry] (backend)}
          <div class="backend">
            <span class="backend-name">{backend}</span>
            {#if !entry.reported}
              <span class="muted">{entry.note}</span>
            {:else}
              <div class="windows">
                {#each entry.windows as window (window.name)}
                  <div class="window" class:warn={window.warn}>
                    <span class="label">{window.label}</span>
                    {#if window.used_percent !== null}
                      <span
                        class="bar"
                        role="progressbar"
                        aria-valuenow={Math.round(window.used_percent)}
                        aria-valuemin="0"
                        aria-valuemax="100"
                        aria-label="{backend} {window.label} used"
                      ><span class="fill" style:width="{Math.min(100, window.used_percent)}%"></span></span>
                    {/if}
                    <span class="detail">{windowText(window, now)}</span>
                  </div>
                {/each}
              </div>
            {/if}
          </div>
        {/each}
      </div>
    {/each}
  </section>
{/if}

<style>
  .card {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
    padding: 1rem 1.25rem;
    background: var(--md-sys-color-surface-container-low);
    border: 1px solid var(--md-sys-color-outline-variant);
    border-radius: var(--md-sys-shape-corner-medium);
  }

  .card :global(p) {
    margin: 0;
  }

  .muted {
    color: var(--md-sys-color-on-surface-variant);
  }

  .flag {
    color: var(--md-sys-color-error);
    font-size: 0.9rem;
  }

  .place {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    padding-top: 0.4rem;
    border-top: 1px solid var(--md-sys-color-outline-variant);
  }

  .name {
    font-weight: 600;
  }

  .backend {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 0.25rem 0.8rem;
  }

  .backend-name {
    min-width: 6rem;
    font-family: var(--font-mono);
    font-size: 0.85rem;
  }

  .windows {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    flex: 1;
    min-width: 0;
  }

  .window {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.2rem 0.6rem;
    font-size: 0.85rem;
  }

  .label {
    min-width: 5.5rem;
  }

  .bar {
    flex: 0 1 8rem;
    min-width: 4rem;
    height: 0.5rem;
    background: var(--md-sys-color-surface-container-highest);
    border-radius: var(--md-sys-shape-corner-full);
    overflow: hidden;
  }

  .fill {
    display: block;
    height: 100%;
    background: var(--md-sys-color-primary);
  }

  .window.warn .fill {
    background: var(--md-sys-color-error);
  }

  .window.warn .detail {
    color: var(--md-sys-color-error);
  }
</style>
