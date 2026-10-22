<script lang="ts">
  // The Chat tab's left rail (CUT-174, docs/design/chat-rework/Main.dc.html): the goal, the plan with
  // its progress, and the run's figures with Pause and Stop. Everything comes from the daemon's
  // Autopilot status; the plan is the checklist file the builder keeps.
  import {
    compactTokens,
    controlLabel,
    controlsFor,
    currentStep,
    elapsedText,
    needsPerson,
    phaseLabel,
    planProgress,
    type AutopilotStatus,
    type Control,
  } from "../autopilot";

  let {
    status,
    busy,
    error,
    onControl,
  }: {
    status: AutopilotStatus;
    busy: boolean;
    error: string | null;
    onControl: (control: Control) => void;
  } = $props();

  const controls = $derived(controlsFor(status.phase));
  const current = $derived(currentStep(status));
  const progress = $derived(planProgress(status));
</script>

<aside class="rail" aria-label="Plan and run">
  {#if status.goal}
    <section>
      <h3 class="label-medium muted">Goal</h3>
      <p class="body-medium goal">{status.goal}</p>
    </section>
  {/if}

  <section>
    <div class="plan-head">
      <h3 class="label-medium muted">Plan</h3>
      {#if status.plan && status.plan.total > 0}
        <span class="label-small muted">{status.plan.done} of {status.plan.total} done</span>
      {/if}
    </div>
    {#if status.plan && status.plan.total > 0}
      <div
        class="bar"
        role="progressbar"
        aria-label="Plan progress"
        aria-valuemin="0"
        aria-valuemax={status.plan.total}
        aria-valuenow={status.plan.done}
      >
        <div class="fill" style:width="{Math.round(progress * 100)}%"></div>
      </div>
      <ol class="steps">
        {#each status.plan.steps as step, index (index)}
          <li class="step" class:done={step.done} class:current={index === current}>
            <span class="mark" aria-hidden="true">{step.done ? "✓" : index === current ? "●" : "○"}</span>
            <span class="body-small">
              <span class="visually-hidden">{step.done ? "Done: " : index === current ? "Now: " : "To do: "}</span>
              {step.text}
            </span>
          </li>
        {/each}
      </ol>
    {:else}
      <p class="body-small muted">
        {status.phase === "planning"
          ? "The builder is writing its plan."
          : "No plan yet. The builder keeps one as a checklist in its plan file."}
      </p>
    {/if}
    <p class="label-small muted file mono">{status.plan_path}</p>
  </section>

  {#if status.phase !== "off"}
    <section>
      <h3 class="label-medium muted">Run</h3>
      <p class="state body-medium" class:attention={needsPerson(status.phase)} role="status">
        {phaseLabel(status.phase)}
      </p>
      {#if status.reason}<p class="body-small muted">{status.reason}</p>{/if}
      {#if status.limits}
        <dl class="figures">
          <div>
            <dt class="body-small">Nudges</dt>
            <dd class="mono">{status.nudges ?? 0} of {status.limits.nudges}</dd>
          </div>
          {#if status.tokens_used != null}
            <div>
              <dt class="body-small">Tokens</dt>
              <dd class="mono">{compactTokens(status.tokens_used)} of {compactTokens(status.limits.tokens)}</dd>
            </div>
          {/if}
          <div>
            <dt class="body-small">Time limit</dt>
            <dd class="mono">
              {#if status.elapsed_s != null}{elapsedText(status.elapsed_s)} of {/if}{status.limits.hours} h
            </dd>
          </div>
        </dl>
      {/if}
      {#if controls.length > 0}
        <div class="controls">
          {#each controls as control (control)}
            <button
              class="btn {control === 'approve' ? 'btn-filled' : 'btn-tonal'}"
              disabled={busy}
              onclick={() => onControl(control)}
            >
              {controlLabel(control, status.phase)}
            </button>
          {/each}
        </div>
      {/if}
      {#if error}<p class="body-small error" role="alert">{error}</p>{/if}
    </section>
  {/if}
</aside>

<style>
  .rail {
    display: flex;
    flex-direction: column;
    gap: 1.25rem;
    min-width: 0;
  }

  h3,
  p {
    margin: 0;
  }

  section {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  .goal {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }

  .plan-head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
  }

  .bar {
    height: 6px;
    border-radius: 3px;
    background: var(--md-sys-color-surface-container-highest);
    overflow: hidden;
  }

  .fill {
    height: 100%;
    background: var(--md-sys-color-primary);
    transition: width 200ms;
  }

  .steps {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  .step {
    display: flex;
    gap: 0.6rem;
    align-items: flex-start;
    color: var(--md-sys-color-on-surface-variant);
  }

  .step.done {
    color: var(--md-sys-color-on-surface);
  }

  .step.current {
    color: var(--md-sys-color-on-surface);
    font-weight: 600;
  }

  .mark {
    width: 1rem;
    text-align: center;
    color: var(--md-sys-color-primary);
    flex: none;
  }

  .file {
    overflow-wrap: anywhere;
    font-size: 0.6875rem;
  }

  .state {
    font-weight: 600;
  }

  .state.attention {
    color: var(--md-sys-color-attention);
  }

  .figures {
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
  }

  .figures div {
    display: flex;
    justify-content: space-between;
    gap: 0.75rem;
  }

  .figures dt {
    color: var(--md-sys-color-on-surface-variant);
  }

  .figures dd {
    margin: 0;
    font-size: 0.8125rem;
  }

  .controls {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }

  .error {
    color: var(--md-sys-color-error);
  }

  .visually-hidden {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
  }
</style>
