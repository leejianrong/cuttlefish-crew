<script lang="ts">
  // "Give the builder a goal" (CUT-171, ADR-0044): one box that starts the project's agent in tmux
  // with the goal as its first message. It replaces "Start a team" as the main way to start work;
  // the round and team workflow is under Advanced on the same page.
  import type { AgentSession, FleetClient, Goal } from "../api";
  import { phaseLabel, type AutopilotStatus } from "../autopilot";
  import { BUILDERS, GOAL_EXAMPLES, builderLabel, canGive, defaultBuilder } from "../goal";
  import type { ProjectTab } from "../route";

  let {
    client,
    projectId,
    projectBackend,
    onTabChange,
  }: {
    client: FleetClient;
    projectId: string;
    projectBackend: string | null | undefined;
    onTabChange: (tab: ProjectTab) => void;
  } = $props();

  let session = $state<AgentSession | null>(null);
  let goal = $state<Goal | null>(null);
  let text = $state("");
  // Read once on purpose: the person's choice, which the project's backend only seeds.
  // svelte-ignore state_referenced_locally
  let builder = $state<string>(defaultBuilder(projectBackend));
  // Autopilot is on by default for a builder given a goal (ADR-0044).
  let autopilotOn = $state(true);
  let hours = $state(24);
  let tokensMillion = $state(5);
  let autopilot = $state<AutopilotStatus | null>(null);
  let busy = $state(false);
  let error = $state<string | null>(null);
  let shownFor = "";

  $effect(() => {
    const id = projectId;
    if (shownFor !== id) {
      session = null;
      goal = null;
      text = "";
      error = null;
      shownFor = id;
    }
    const read = async () => {
      try {
        const [nextSession, nextGoal, nextAutopilot] = await Promise.all([
          client.getAgentSession(id),
          client.getGoal(id),
          client.getAutopilot(id),
        ]);
        if (id === projectId) {
          session = nextSession;
          goal = nextGoal;
          autopilot = nextAutopilot;
        }
      } catch {
        // The page's own connection banner says so; this card keeps what it last read.
      }
    };
    void read();
    const timer = setInterval(read, 5000);
    return () => clearInterval(timer);
  });

  const running = $derived(session?.state === "running");

  async function give() {
    if (busy || !canGive(text)) return;
    busy = true;
    error = null;
    try {
      const result = await client.giveGoal(
        projectId,
        text,
        builder,
        autopilotOn
          ? { hours: Number(hours), tokens: Math.round(Number(tokensMillion) * 1_000_000) }
          : false,
      );
      session = result.session;
      goal = result.goal;
      text = "";
      autopilot = await client.getAutopilot(projectId).catch(() => autopilot);
    } catch (e) {
      error = e instanceof Error ? e.message : "That didn't work.";
    } finally {
      busy = false;
    }
  }

  function onKey(event: KeyboardEvent) {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      void give();
    }
  }
</script>

<section class="card filled goal" aria-labelledby="goal-heading">
  <h2 id="goal-heading" class="title-large">Give the builder a goal</h2>

  {#if running}
    <div class="current" data-testid="current-goal">
      {#if goal}
        <p class="label-medium muted">{builderLabel(session?.backend ?? goal.backend)} is working on</p>
        <p class="body-large goal-text">{goal.text}</p>
      {:else}
        <p class="body-medium">
          {builderLabel(session?.backend)} is running in tmux, and no goal was given from here.
        </p>
      {/if}
      {#if autopilot && autopilot.phase !== "off"}
        <p class="body-medium" data-testid="autopilot-line">
          Autopilot: <strong>{phaseLabel(autopilot.phase)}</strong>{autopilot.plan && autopilot.plan.total
            ? `, ${autopilot.plan.done} of ${autopilot.plan.total} steps`
            : ""}.
          {#if autopilot.reason}<span class="muted">{autopilot.reason}</span>{/if}
        </p>
      {/if}
      <p class="body-small muted">
        Talk to it and see its plan in the Chat tab, or watch it work in the Terminal tab. Permission
        questions appear under Needs you.
      </p>
      <div class="actions">
        <button class="btn btn-filled open" onclick={() => onTabChange("chat")}>Open the chat</button>
        <button class="btn btn-tonal open" onclick={() => onTabChange("terminal")}>Open the terminal</button>
      </div>
    </div>
  {:else}
    <p class="body-medium muted">
      One agent works on this project in its own terminal, and keeps going if you close the browser.
      Say what you want done; it starts with this as its first message.
    </p>
    {#if goal}
      <p class="body-small muted" data-testid="last-goal">Last goal: {goal.text}</p>
    {/if}
    <div class="examples">
      <span class="label-medium muted">Try</span>
      {#each GOAL_EXAMPLES as example (example)}
        <button type="button" class="chip" onclick={() => (text = example)}>{example}</button>
      {/each}
    </div>
    <label class="field">
      <span class="field-label">Goal</span>
      <textarea
        bind:value={text}
        onkeydown={onKey}
        rows="3"
        placeholder="What should the builder do?"
      ></textarea>
    </label>
    <div class="who" role="group" aria-label="Which builder">
      {#each BUILDERS as choice (choice.id)}
        <button
          type="button"
          class="chip"
          class:selected={builder === choice.id}
          aria-pressed={builder === choice.id}
          onclick={() => (builder = choice.id)}
        >
          {choice.label}
        </button>
      {/each}
    </div>
    <div class="autopilot">
      <label class="switch-row">
        <input type="checkbox" role="switch" bind:checked={autopilotOn} />
        <span class="switch-text">
          <span class="title-small">Autopilot</span>
          <span class="body-small muted">
            {#if autopilotOn}
              The builder plans first and you approve the plan. Then it keeps going, and the daemon
              sends it "continue" whenever it stops with steps left, until the plan is done or a limit
              below is reached. It asks you only if it is blocked or needs a permission, and Pause and
              Stop are always there.
            {:else}
              The builder starts on your goal and stops when it is done or has a question.
            {/if}
          </span>
        </span>
      </label>
      {#if autopilotOn}
        <div class="limits">
          <label class="mini">
            <span class="label-small muted">Time limit (hours)</span>
            <input type="number" min="0.5" max="720" step="0.5" bind:value={hours} />
          </label>
          <label class="mini">
            <span class="label-small muted">Token limit (millions)</span>
            <input type="number" min="0.1" max="1000" step="0.5" bind:value={tokensMillion} />
          </label>
        </div>
      {/if}
    </div>
    {#if projectBackend === "kopicode"}
      <p class="body-small muted">
        This project's own backend is kopicode, whose interactive session cannot take a first message
        yet. Pick Claude Code or Codex for a goal; kopicode still runs rounds under Advanced.
      </p>
    {/if}
    {#if error}<p class="body-small error" role="alert">{error}</p>{/if}
    <button class="btn btn-filled give" disabled={busy || !canGive(text)} onclick={give}>
      {busy ? "Starting…" : "Give the builder this goal"}
    </button>
  {/if}
</section>

<style>
  .goal {
    padding: 1.5rem;
    margin-bottom: 1.5rem;
    display: flex;
    flex-direction: column;
    gap: 1rem;
    max-width: 56rem;
  }

  .goal h2,
  .goal p {
    margin: 0;
  }

  .goal-text {
    white-space: pre-wrap;
  }

  .current {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    align-items: flex-start;
  }

  .goal :global(.field-label) {
    background: var(--md-sys-color-surface-container-low);
  }

  .field textarea {
    line-height: 1.5rem;
  }

  /* An example sentence can be longer than a phone: wrap it rather than run off the screen. */
  .examples,
  .who {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
  }

  .examples :global(.chip) {
    height: auto;
    min-height: 32px;
    max-width: 100%;
    padding-block: 6px;
    white-space: normal;
    text-align: left;
  }

  .give {
    align-self: flex-start;
  }

  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }

  .autopilot {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }

  .switch-row {
    display: flex;
    gap: 0.75rem;
    align-items: flex-start;
    cursor: pointer;
  }

  /* A 44px target (the whole row is the label) around a 1.5rem box. */
  .switch-row input {
    width: 1.5rem;
    height: 1.5rem;
    margin: 0.6rem;
    flex: none;
    accent-color: var(--md-sys-color-primary);
    cursor: pointer;
  }

  .switch-text {
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
  }

  .limits {
    display: flex;
    flex-wrap: wrap;
    gap: 1rem;
  }

  .mini {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
  }

  .mini input {
    width: 9rem;
    min-height: 2.75rem;
    padding: 0 0.75rem;
    border: 1px solid var(--md-sys-color-outline);
    border-radius: var(--md-sys-shape-corner-small, 8px);
    background: transparent;
    color: inherit;
    font: inherit;
  }

  .error {
    color: var(--md-sys-color-error);
  }
</style>
