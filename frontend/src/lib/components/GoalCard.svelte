<script lang="ts">
  // "Give the builder a goal" (CUT-171, ADR-0044): one box that starts the project's agent in tmux
  // with the goal as its first message. It replaces "Start a team" as the main way to start work;
  // the round and team workflow is under Advanced on the same page.
  import type { AgentSession, FleetClient, Goal } from "../api";
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
        const [nextSession, nextGoal] = await Promise.all([
          client.getAgentSession(id),
          client.getGoal(id),
        ]);
        if (id === projectId) {
          session = nextSession;
          goal = nextGoal;
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
      const result = await client.giveGoal(projectId, text, builder);
      session = result.session;
      goal = result.goal;
      text = "";
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
      <p class="body-small muted">
        Talk to it, answer its questions and watch it work in the Terminal tab. Permission questions
        appear under Needs you.
      </p>
      <button class="btn btn-tonal open" onclick={() => onTabChange("terminal")}>Open the terminal</button>
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

  .error {
    color: var(--md-sys-color-error);
  }
</style>
