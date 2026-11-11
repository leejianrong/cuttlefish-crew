<script lang="ts">
  // One line at the top of the project's Overview (CUT-286): what is running, how it last came up,
  // tokens and time, the goal's branch with its diff stat, and the pull request. It reads the same
  // endpoints the cards below it do (session, Autopilot, goal) plus the diff's counts alone, never
  // the patch, and keeps nothing. Silent for a project that has never been given work.
  import type { AgentSession, FleetClient, Goal, GoalDiffResult, ProjectSummary } from "../api";
  import type { AutopilotStatus } from "../autopilot";
  import { stripParts } from "../statusstrip";

  let {
    client,
    projectId,
    project,
    refreshKey = 0,
  }: { client: FleetClient; projectId: string; project: ProjectSummary; refreshKey?: number } =
    $props();

  let session = $state<AgentSession | null>(null);
  let autopilot = $state<AutopilotStatus | null>(null);
  let goal = $state<Goal | null>(null);
  let stat = $state<GoalDiffResult | null>(null);
  let shownFor = "";

  $effect(() => {
    const id = projectId;
    void refreshKey;
    if (shownFor !== id) {
      session = autopilot = goal = stat = null;
      shownFor = id;
    }
    const read = async () => {
      const [s, a, g] = await Promise.all([
        client.getAgentSession(id).catch(() => null),
        client.getAutopilot(id).catch(() => null),
        client.getGoal(id).catch(() => null),
      ]);
      // A branch is worth a stat; no branch, or a worker's, is not asked about.
      const next = g?.branch && !project.worker ? await client.getGoalStat(id).catch(() => null) : null;
      if (id !== projectId) return;
      session = s;
      autopilot = a;
      goal = g;
      stat = next;
    };
    void read();
    const timer = setInterval(read, 5000);
    return () => clearInterval(timer);
  });

  const parts = $derived(
    stripParts({
      session,
      teamRunning: project.running,
      usage: project.usage,
      autopilot,
      goal,
      stat,
    }),
  );
</script>

{#if parts.length > 0}
  <section class="strip" aria-label="Status" data-testid="status-strip">
    <ul>
      {#each parts as part (part.key)}
        <li class="label-large {part.tone}" class:mono={part.mono} data-part={part.key}>
          {#if part.href}
            <a href={part.href} target="_blank" rel="noopener noreferrer">{part.text}</a>
          {:else}
            {part.text}
          {/if}
        </li>
      {/each}
    </ul>
  </section>
{/if}

<style>
  .strip {
    margin-bottom: 1rem;
    padding: 0.5rem 0.75rem;
    border-radius: var(--md-sys-shape-corner-medium, 12px);
    background: var(--md-sys-color-surface-container-low);
    max-width: 56rem;
  }

  ul {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.25rem 1.25rem;
  }

  li {
    min-width: 0;
    overflow-wrap: anywhere;
    color: var(--md-sys-color-on-surface-variant);
  }

  .mono {
    font-size: 0.8125rem;
  }

  .ok {
    color: var(--md-sys-color-success);
    font-weight: 600;
  }

  .attention {
    color: var(--md-sys-color-error);
    font-weight: 600;
  }

  a {
    color: var(--md-sys-color-primary);
  }
</style>
