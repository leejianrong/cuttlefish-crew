<script lang="ts">
  // A project's docker compose stack on its worker (CUT-216): start it, see the ports it published, open
  // one in the dashboard, stop it. A page is fetched by the worker and shown in the same sandboxed frame
  // as an agent's design, so it runs with no access to the dashboard, the network or its own assets:
  // a page that needs files beside it shows without them.
  import { FleetApiError, type FleetClient, type StackInfo } from "../api";
  import { FRAME_SANDBOX, htmlFrameDoc } from "../artifacts";
  import { portLabel, stackLine } from "../stack";

  const POLL_MS = 2000;

  let { client, projectId }: { client: FleetClient; projectId: string } = $props();

  let stack = $state<StackInfo | null>(null);
  let working = $state(false);
  let error = $state<string | null>(null);
  let open = $state<{ port: number; doc: string } | null>(null);
  let opening = $state<number | null>(null);

  function say(failure: unknown): string {
    return failure instanceof FleetApiError || failure instanceof Error ? failure.message : "That did not work.";
  }

  async function load() {
    try {
      stack = (await client.getStack(projectId)).stack;
    } catch {
      /* the next tick tries again */
    }
  }

  // Read the stack when the project changes, then every couple of seconds. This effect must not read
  // `stack` itself, or each answer would re-run it and close the page the person opened.
  $effect(() => {
    void projectId;
    open = null;
    void load();
    const timer = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(timer);
  });

  async function act(call: () => Promise<{ stack: StackInfo | null }>) {
    working = true;
    error = null;
    try {
      stack = (await call()).stack;
    } catch (failure) {
      error = say(failure);
    } finally {
      working = false;
    }
  }

  async function show(port: number) {
    opening = port;
    error = null;
    try {
      const html = await (await client.readStackPage(projectId, port, "/")).text();
      open = { port, doc: htmlFrameDoc(html) };
    } catch (failure) {
      error = say(failure);
    } finally {
      opening = null;
    }
  }

  const running = $derived(stack !== null && stack.state !== "down");
</script>

<div class="stack">
  <p class="body-small">
    <span class="label-medium muted">Compose stack</span>
    <span data-testid="stack-line" class:problem={stack?.state === "failed"}>{stackLine(stack)}</span>
  </p>
  <div class="actions">
    {#if !running || stack?.state === "failed"}
      <button type="button" disabled={working} onclick={() => act(() => client.startStack(projectId))}>
        Start the stack
      </button>
    {/if}
    {#if running}
      <button
        type="button"
        class="quiet"
        disabled={working || stack?.state === "stopping"}
        onclick={() => {
          open = null;
          void act(() => client.stopStack(projectId));
        }}
      >
        Stop the stack
      </button>
    {/if}
    {#if stack?.state === "up"}
      {#each stack.ports as port (port.published)}
        <button type="button" disabled={opening !== null} onclick={() => show(port.published)}>
          Open {portLabel(port)}
        </button>
      {/each}
    {/if}
  </div>
  {#if error}<p class="body-small problem" role="status">{error}</p>{/if}
  {#if open && stack?.state === "up"}
    <iframe title="Stack page, port {open.port}" sandbox={FRAME_SANDBOX} srcdoc={open.doc} referrerpolicy="no-referrer"></iframe>
    <p class="body-small muted">
      Runs in a sandbox: scripts work, but it cannot reach the network, the dashboard or your files, so files
      the page loads from the stack are blocked. Stopping the project stops the stack.
    </p>
  {/if}
</div>

<style>
  .stack {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    margin: 0.5rem 0;
  }

  p {
    margin: 0;
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    align-items: baseline;
  }

  .muted {
    color: var(--text-muted);
  }

  .problem {
    color: var(--md-sys-color-error);
  }

  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }

  .actions button {
    min-height: 2.5rem;
    padding: 0.4rem 1rem;
    border: 1px solid var(--md-sys-color-outline);
    border-radius: var(--md-sys-shape-corner-full);
    background: transparent;
    color: var(--text);
    cursor: pointer;
  }

  .actions button:disabled {
    opacity: 0.5;
    cursor: default;
  }

  .actions .quiet {
    border-color: transparent;
  }

  iframe {
    width: 100%;
    height: 22rem;
    border: 1px solid var(--md-sys-color-outline-variant);
    border-radius: var(--md-sys-shape-corner-small);
    background: var(--md-sys-color-surface);
  }
</style>
