<script lang="ts">
  // The plan, in the thread, waiting for approval (CUT-174). Approving is the one decision Autopilot
  // asks of a person before it keeps the builder going; changing the plan is a message to the builder.
  import { REVISE_HINT, type AutopilotStatus } from "../autopilot";

  let {
    status,
    busy,
    onApprove,
  }: { status: AutopilotStatus; busy: boolean; onApprove: () => void } = $props();
</script>

<section class="plan card filled" aria-labelledby="plan-card-heading">
  <div class="head">
    <h3 id="plan-card-heading" class="title-small">
      Plan, {status.plan?.total ?? 0} {status.plan?.total === 1 ? "step" : "steps"}
    </h3>
    <span class="pill label-small">Waiting for you</span>
  </div>
  <ol class="steps">
    {#each status.plan?.steps ?? [] as step, index (index)}
      <li class="body-medium">{step.text}</li>
    {/each}
  </ol>
  <p class="body-small muted">
    Once you approve, the builder works through these on its own and the daemon sends it "continue"
    whenever it stops with steps left, until the plan is done or a limit is reached. Pause and Stop are
    always in the rail. {REVISE_HINT}
  </p>
  <button class="btn btn-filled" disabled={busy} onclick={onApprove}>
    {busy ? "Approving…" : "Approve the plan"}
  </button>
</section>

<style>
  .plan {
    padding: 1rem 1.25rem;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    align-items: flex-start;
  }

  .head {
    display: flex;
    gap: 0.75rem;
    align-items: center;
    flex-wrap: wrap;
  }

  h3,
  p {
    margin: 0;
  }

  .pill {
    padding: 0.15rem 0.6rem;
    border-radius: 0.8rem;
    background: var(--md-sys-color-attention-container);
    color: var(--md-sys-color-on-attention-container);
  }

  .steps {
    margin: 0;
    padding-left: 1.25rem;
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
  }
</style>
