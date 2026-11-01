<script lang="ts">
  // The Overview's mission-control part (CUT-176, docs/design/chat-rework/MissionControl.dc.html): the
  // goal with a progress ring, what happened while you were away, and a timeline. Everything is
  // derived by the daemon from the journal, the artifacts folder, the repository and the builder's
  // transcript; this only draws it. It shows nothing until there is a goal or something on the
  // timeline, so a project that has never been given work looks as it did.
  import {
    compactTokens,
    controlLabel,
    controlsFor,
    phaseLabel,
    needsPerson,
    planProgress,
    type Control,
  } from "../autopilot";
  import type { FleetClient } from "../api";
  import {
    awayWorthShowing,
    dotTone,
    eventWhen,
    loadLastVisit,
    nowLine,
    ringOffset,
    saveLastVisit,
    sinceText,
    type OverviewData,
    type TimelineEvent,
  } from "../overview";

  let {
    client,
    projectId,
    active,
    onTabChange,
    onAutopilotChange = () => {},
  }: {
    client: FleetClient;
    projectId: string;
    active: boolean;
    onTabChange: (tab: "chat" | "needs-you" | "artifacts" | "terminal") => void;
    onAutopilotChange?: () => void;
  } = $props();

  let data = $state<OverviewData | null>(null);
  // Fixed for a visit: what is "new" is what happened since you were last here, and it should not
  // move under you while you read it. Saved when you leave.
  let since = $state("");
  let shownFor = "";
  let busy = $state(false);
  let error = $state<string | null>(null);

  const RADIUS = 26;
  const circumference = 2 * Math.PI * RADIUS;

  $effect(() => {
    const id = projectId;
    if (shownFor !== id) {
      data = null;
      shownFor = id;
    }
    if (!active) return;
    // A local copy for the reads: the effect must not depend on the state it sets.
    const start = loadLastVisit(id);
    since = start;
    let stop = false;
    const read = async () => {
      try {
        const next = await client.getOverview(id, start);
        if (!stop && id === projectId) data = next;
      } catch {
        // The page's own connection banner says so.
      }
    };
    void read();
    const timer = setInterval(read, 5000);
    return () => {
      stop = true;
      clearInterval(timer);
      saveLastVisit(id);
    };
  });

  async function control(action: Control) {
    if (busy) return;
    busy = true;
    error = null;
    try {
      await client.autopilotAction(projectId, action);
      onAutopilotChange();
      data = await client.getOverview(projectId, since);
    } catch (e) {
      error = e instanceof Error ? e.message : "That didn't work.";
    } finally {
      busy = false;
    }
  }

  function open(event: TimelineEvent) {
    if (event.kind === "needs_you") onTabChange("needs-you");
    else if (event.kind === "image") onTabChange("artifacts");
    else if (event.kind === "refresh" || event.kind === "note") onTabChange("chat");
  }

  const live = $derived(data !== null && data.autopilot.phase !== "off");
  const away = $derived(
    data !== null && awayWorthShowing(since, data.digest) ? data.digest : [],
  );
</script>

{#if data && (live || data.timeline.length > 0)}
  {#if live}
    <section class="card filled banner" aria-labelledby="mc-goal">
      <svg class="ring" width="64" height="64" viewBox="0 0 64 64" role="img"
        aria-label="Plan progress: {data.autopilot.plan?.done ?? 0} of {data.autopilot.plan?.total ?? 0} steps">
        <circle cx="32" cy="32" r={RADIUS} fill="none" stroke-width="6" class="track" />
        <circle
          cx="32"
          cy="32"
          r={RADIUS}
          fill="none"
          stroke-width="6"
          stroke-linecap="round"
          class="arc"
          stroke-dasharray={circumference}
          stroke-dashoffset={ringOffset(planProgress(data.autopilot), RADIUS)}
          transform="rotate(-90 32 32)"
        />
        <text x="32" y="36" text-anchor="middle" class="ring-text">
          {data.autopilot.plan?.total ? `${data.autopilot.plan.done}/${data.autopilot.plan.total}` : ""}
        </text>
      </svg>
      <div class="goal">
        <h2 id="mc-goal" class="title-medium">{data.autopilot.goal ?? data.goal?.text ?? "Goal"}</h2>
        <p class="body-medium muted">{nowLine(data.autopilot)}</p>
        <div class="pills">
          <span class="pill" class:attention={needsPerson(data.autopilot.phase)}>
            {phaseLabel(data.autopilot.phase)}
          </span>
          {#if data.autopilot.limits}
            <span class="pill">Time limit {data.autopilot.limits.hours} h</span>
            {#if data.autopilot.tokens_used != null}
              <span class="pill">
                Tokens {compactTokens(data.autopilot.tokens_used)} of {compactTokens(data.autopilot.limits.tokens)}
              </span>
            {/if}
          {/if}
        </div>
      </div>
      <div class="buttons">
        {#each controlsFor(data.autopilot.phase).filter((c) => c !== "nudge") as action (action)}
          <button
            class="btn {action === 'approve' ? 'btn-filled' : 'btn-tonal'}"
            disabled={busy}
            onclick={() => (action === "approve" ? onTabChange("chat") : control(action))}
          >
            {action === "approve" ? "Review the plan" : controlLabel(action, data.autopilot.phase)}
          </button>
        {/each}
      </div>
      {#if error}<p class="body-small error" role="alert">{error}</p>{/if}
    </section>
  {/if}

  {#if away.length > 0}
    <section class="card filled digest" aria-labelledby="mc-away">
      <div class="head">
        <h2 id="mc-away" class="title-medium">While you were away</h2>
        <span class="body-small muted">{sinceText(since)}</span>
      </div>
      <ul>
        {#each away as line, n (n)}<li class="body-medium">{line}</li>{/each}
      </ul>
    </section>
  {/if}

  {#if data.timeline.length > 0}
    <section class="card filled timeline" aria-labelledby="mc-timeline">
      <h2 id="mc-timeline" class="title-medium">Timeline</h2>
      <ol>
        {#each data.timeline.slice(0, 24) as event (`${event.at}|${event.kind}|${event.text}`)}
          <li class="ev">
            <span class="when mono label-small">{eventWhen(event.at)}</span>
            <span class="dot {dotTone(event.kind)}" aria-hidden="true"></span>
            <span class="text body-medium">
              {event.text}
              {#if event.kind === "commit" && event.ref}<span class="mono label-small muted">{event.ref}</span>{/if}
              {#if event.kind === "needs_you" || event.kind === "image"}
                <button class="link" onclick={() => open(event)}>
                  {event.kind === "needs_you" ? "Review" : "Open"}
                </button>
              {/if}
            </span>
          </li>
        {/each}
      </ol>
    </section>
  {/if}
{/if}

<style>
  section {
    max-width: 56rem;
    margin-bottom: 1.5rem;
  }

  .banner {
    padding: 1.25rem 1.5rem;
    display: flex;
    gap: 1.25rem;
    align-items: center;
    flex-wrap: wrap;
  }

  .ring {
    flex: none;
  }

  .track {
    stroke: var(--md-sys-color-surface-container-highest);
  }

  .arc {
    stroke: var(--md-sys-color-primary);
    transition: stroke-dashoffset 300ms;
  }

  .ring-text {
    font: 600 0.8rem var(--md-sys-typescale-body-font, system-ui);
    fill: var(--md-sys-color-on-surface);
  }

  .goal {
    flex: 1 1 16rem;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
  }

  h2,
  p {
    margin: 0;
  }

  .pills {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
  }

  .pill {
    padding: 0.15rem 0.65rem;
    border-radius: 0.8rem;
    font-size: 0.75rem;
    background: var(--md-sys-color-surface-container-highest);
    color: var(--md-sys-color-on-surface-variant);
  }

  .pill.attention {
    background: var(--md-sys-color-attention-container);
    color: var(--md-sys-color-on-attention-container);
  }

  .buttons {
    display: flex;
    gap: 0.5rem;
    flex-wrap: wrap;
  }

  .error {
    flex-basis: 100%;
    color: var(--md-sys-color-error);
  }

  .digest,
  .timeline {
    padding: 1.1rem 1.25rem;
  }

  .head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 0.75rem;
    flex-wrap: wrap;
    margin-bottom: 0.5rem;
  }

  ul {
    margin: 0;
    padding-left: 1.2rem;
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
  }

  ol {
    list-style: none;
    margin: 0.75rem 0 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 0.85rem;
  }

  .ev {
    display: flex;
    gap: 0.9rem;
    align-items: flex-start;
  }

  .when {
    flex: 0 0 5.5rem;
    padding-top: 0.2rem;
    color: var(--md-sys-color-on-surface-variant);
  }

  .dot {
    flex: 0 0 0.75rem;
    width: 0.75rem;
    height: 0.75rem;
    border-radius: 50%;
    margin-top: 0.35rem;
    background: var(--md-sys-color-outline);
  }

  .dot.accent {
    background: var(--md-sys-color-primary);
  }

  .dot.attention {
    background: var(--md-sys-color-attention);
  }

  .text {
    min-width: 0;
    overflow-wrap: anywhere;
  }

  .link {
    margin-left: 0.5rem;
    padding: 0;
    border: 0;
    background: none;
    color: var(--md-sys-color-primary);
    font: inherit;
    text-decoration: underline;
    cursor: pointer;
    min-height: 2.75rem;
  }

  @media (max-width: 640px) {
    .when {
      flex-basis: 4rem;
    }
  }
</style>
