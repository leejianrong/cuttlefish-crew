<script lang="ts">
  // The Chat tab (CUT-172 to CUT-175, CUT-179, ADR-0044): the agent's conversation, read from its own
  // session record (Claude Code's transcript) and never stored a second time, with a box that types
  // into the same session, the plan rail and the artifact canvas. A permission the builder is
  // waiting on is answered inline, with the same card as Needs you. On a phone it is one column:
  // a plan strip, the thread scrolling with the page, and the message box pinned above the bottom bar.
  import type { FleetClient, NeedsYouRequest } from "../api";
  import { FleetApiError } from "../api";
  import {
    controlLabel,
    controlsFor,
    phaseLabel,
    type AutopilotStatus,
    type Control,
  } from "../autopilot";
  import {
    EMPTY_CHAT,
    applyChat,
    chatNotice,
    clockTime,
    composerState,
    toRows,
    type ChatResponse,
    type ChatState,
  } from "../chat";
  import { parseMarkdown } from "../markdown";
  import Icon from "./Icon.svelte";
  import ArtifactCanvas from "./ArtifactCanvas.svelte";
  import Markdown from "./Markdown.svelte";
  import PlanCard from "./PlanCard.svelte";
  import PlanRail from "./PlanRail.svelte";
  import RequestCard from "./RequestCard.svelte";

  let {
    client,
    projectId,
    active,
    pending,
    fetchedAt,
    onAnswered,
    offline,
    onOpenTerminal,
    onOpenArtifacts,
  }: {
    client: FleetClient;
    projectId: string;
    active: boolean;
    pending: NeedsYouRequest[];
    fetchedAt: number;
    onAnswered: () => void;
    offline: boolean;
    onOpenTerminal: () => void;
    onOpenArtifacts: () => void;
  } = $props();

  let thread = $state<ChatState>(EMPTY_CHAT);
  let latest = $state<ChatResponse | null>(null);
  let failed = $state(false);
  let shownFor = "";
  let scroller: HTMLDivElement | undefined = $state();
  // Whether the reader is at the foot of the thread: new messages scroll into view only then.
  let atFoot = $state(true);
  let unseen = $state(0);

  const rows = $derived(toRows(thread.items));
  const notice = $derived(failed && latest === null ? "Couldn't read the conversation." : chatNotice(latest, thread.items.length));
  // Which tool rows the reader opened. A plain set of ids: a row keeps its id as the thread grows.
  let opened = $state<Record<string, boolean>>({});

  // One column on a phone (CUT-179): the page scrolls, not the thread, so there is no second scroll
  // area to fight with, and the message box is pinned above the bottom bar.
  let phone = $state(typeof window !== "undefined" && window.matchMedia("(max-width: 640px)").matches);
  let navPx = $state(0);

  function nearFoot(): boolean {
    if (phone) {
      const page = document.documentElement;
      return page.scrollHeight - window.scrollY - window.innerHeight < 160;
    }
    if (!scroller) return true;
    return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 80;
  }

  function jump() {
    if (phone) {
      window.scrollTo({ top: document.documentElement.scrollHeight });
    } else if (scroller) {
      scroller.scrollTop = scroller.scrollHeight;
    }
    atFoot = true;
    unseen = 0;
  }

  $effect(() => {
    const id = projectId;
    if (shownFor !== id) {
      thread = EMPTY_CHAT;
      latest = null;
      opened = {};
      unseen = 0;
      shownFor = id;
    }
    if (!active) return;
    let stop = false;
    const read = async () => {
      try {
        const response = await client.getChat(id, thread.cursor);
        if (stop || id !== projectId) return;
        failed = false;
        latest = response;
        const before = thread.items.length;
        const wasAtFoot = nearFoot();
        thread = applyChat(thread, response);
        const added = thread.items.length - before;
        if (added > 0) {
          if (before === 0 || wasAtFoot) {
            queueMicrotask(jump);
          } else {
            unseen += added;
            atFoot = false;
          }
        }
      } catch {
        if (!stop) failed = true;
      }
    };
    void read();
    const timer = setInterval(read, 1500);
    return () => {
      stop = true;
      clearInterval(timer);
    };
  });

  function onScroll() {
    atFoot = nearFoot();
    if (atFoot) unseen = 0;
  }

  // Autopilot: the plan rail and the plan card (CUT-174). Read on its own, slower beat: it changes
  // when the builder ticks a step, not as each message arrives.
  let autopilot = $state<AutopilotStatus | null>(null);
  let controlBusy = $state(false);
  let controlError = $state<string | null>(null);
  let wide = $state(typeof window !== "undefined" && window.matchMedia("(min-width: 62rem)").matches);
  // Three columns (rail, thread, canvas) need more room than the rest of the page allows.
  let huge = $state(typeof window !== "undefined" && window.matchMedia("(min-width: 84rem)").matches);

  $effect(() => {
    const media = window.matchMedia("(min-width: 62rem)");
    const big = window.matchMedia("(min-width: 84rem)");
    const small = window.matchMedia("(max-width: 640px)");
    const follow = () => {
      wide = media.matches;
      huge = big.matches;
      phone = small.matches;
      navPx = document.getElementById("shell-nav")?.offsetHeight ?? 0;
    };
    follow();
    media.addEventListener("change", follow);
    big.addEventListener("change", follow);
    small.addEventListener("change", follow);
    window.addEventListener("resize", follow);
    return () => {
      media.removeEventListener("change", follow);
      big.removeEventListener("change", follow);
      small.removeEventListener("change", follow);
      window.removeEventListener("resize", follow);
    };
  });

  // The page's own scroll is the thread's on a phone.
  $effect(() => {
    if (!phone || !active) return;
    const onPage = () => onScroll();
    window.addEventListener("scroll", onPage, { passive: true });
    return () => window.removeEventListener("scroll", onPage);
  });

  // A file the builder made, opened from the thread (CUT-179): the canvas shows it, and on a narrow
  // screen the folded canvas opens and scrolls into view.
  let canvasOpen = $state(false);
  let pinned = $state<string | null>(null);
  let canvasEl: HTMLDetailsElement | undefined = $state();

  function showMade(path: string) {
    pinned = path;
    canvasOpen = true;
    queueMicrotask(() => canvasEl?.scrollIntoView({ block: "start", behavior: "smooth" }));
  }

  $effect(() => {
    const id = projectId;
    if (!active) return;
    let stop = false;
    const read = async () => {
      try {
        const next = await client.getAutopilot(id);
        if (!stop && id === projectId) autopilot = next;
      } catch {
        // The thread's own notice covers a daemon that cannot be reached.
      }
    };
    void read();
    const timer = setInterval(read, 3000);
    return () => {
      stop = true;
      clearInterval(timer);
    };
  });

  async function control(action: Control) {
    if (controlBusy) return;
    controlBusy = true;
    controlError = null;
    try {
      autopilot = await client.autopilotAction(projectId, action);
    } catch (e) {
      controlError = e instanceof Error ? e.message : "That didn't work.";
    } finally {
      controlBusy = false;
    }
  }

  const showRail = $derived(autopilot !== null && (autopilot.phase !== "off" || autopilot.plan !== null));

  // The message box (CUT-173).
  let draft = $state("");
  let sending = $state(false);
  let sendError = $state<string | null>(null);
  let sentNote = $state<string | null>(null);
  const composer = $derived(composerState(latest));

  async function send() {
    const text = draft.trim();
    if (!text || sending || !composer?.enabled) return;
    sending = true;
    sendError = null;
    sentNote = null;
    try {
      const result = await client.sendChat(projectId, text);
      draft = "";
      sentNote =
        result.sent === "queued"
          ? "Queued. The builder reads it at its next step, and it appears above then."
          : "Sent.";
    } catch (e) {
      sendError =
        e instanceof FleetApiError && e.status === 403
          ? "This device is not allowed to type to the builder. Raise it to Terminal under Devices on the PC."
          : e instanceof Error
            ? e.message
            : "That didn't work.";
    } finally {
      sending = false;
    }
  }

  function onComposerKey(event: KeyboardEvent) {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      void send();
    }
  }

  function toggle(id: string) {
    opened[id] = !opened[id];
  }
</script>

<div class="layout" class:with-rail={showRail}>
{#if showRail && autopilot}
  <details class="rail-wrap" open={wide}>
    <summary class="rail-summary body-medium">
      <span class="strip-top">
        <span class="strip-title">
          {#if autopilot.plan && autopilot.plan.total > 0}
            Plan: {autopilot.plan.steps.find((s) => !s.done)?.text ?? "every step done"}
          {:else}
            Plan
          {/if}
        </span>
        <span class="muted strip-count">
          {autopilot.plan && autopilot.plan.total > 0
            ? `${autopilot.plan.done} of ${autopilot.plan.total}`
            : phaseLabel(autopilot.phase)}
        </span>
      </span>
      {#if autopilot.plan && autopilot.plan.total > 0}
        <span class="strip-bar" aria-hidden="true">
          <span class="strip-fill" style:width="{(autopilot.plan.done / autopilot.plan.total) * 100}%"></span>
        </span>
      {/if}
    </summary>
    <PlanRail status={autopilot} busy={controlBusy} error={controlError} onControl={control} />
  </details>
  {#if !wide && controlsFor(autopilot.phase).length > 0}
    <!-- Pause and Stop stay in reach on a phone, where the rail is folded away. -->
    <div class="quick" role="group" aria-label="Autopilot">
      {#each controlsFor(autopilot.phase) as action (action)}
        <button
          class="btn {action === 'approve' ? 'btn-filled' : 'btn-tonal'}"
          disabled={controlBusy}
          onclick={() => control(action)}
        >
          {controlLabel(action, autopilot.phase)}
        </button>
      {/each}
    </div>
  {/if}
{/if}

<section class="chat" aria-labelledby="chat-heading">
  <h2 id="chat-heading" class="visually-hidden">Conversation</h2>

  <div class="scroller" bind:this={scroller} onscroll={onScroll} data-testid="chat-thread" tabindex="-1">
    {#if latest?.earlier}
      <p class="body-small muted center">
        Earlier messages are in the session's record file. Showing the most recent part.
      </p>
    {/if}
    {#each rows as row (row.id)}
      {#if row.t === "message"}
        <article class="message" class:you={row.who === "you"}>
          <header class="label-medium muted">
            {row.who === "you" ? "You" : "Builder"}{clockTime(row.at) ? `, ${clockTime(row.at)}` : ""}
          </header>
          {#if row.who === "you"}
            <p class="body-large plain">{row.text}</p>
          {:else}
            <div class="md"><Markdown blocks={parseMarkdown(row.text)} {client} {projectId} /></div>
          {/if}
        </article>
      {:else if row.t === "tools"}
        <div class="tools">
          <button
            type="button"
            class="tools-head"
            aria-expanded={opened[row.id] === true}
            onclick={() => toggle(row.id)}
          >
            <span class="chev" class:open={opened[row.id]}><Icon name="chevron-right" size={16} /></span>
            <span class="body-medium">{row.label}</span>
            {#if row.failed > 0}
              <span class="failed label-small">{row.failed} failed</span>
            {/if}
            <span class="grow"></span>
            <span class="label-small muted">{clockTime(row.at)}</span>
          </button>
          {#if opened[row.id]}
            <ul class="calls">
              {#each row.calls as item (item.toolId)}
                <li>
                  <p class="call mono">
                    <span class="name">{item.name}</span>
                    {#if item.summary}<span class="summary">{item.summary}</span>{/if}
                  </p>
                  {#if item.result && item.result.text}
                    <pre class="result mono" class:bad={item.result.isError}>{item.result.text}</pre>
                  {/if}
                </li>
              {/each}
            </ul>
          {/if}
        </div>
        {#each row.made as file (file.path)}
          <!-- Shown even when the calls are folded: what the builder made is the point of the run. -->
          <button type="button" class="made" onclick={() => showMade(file.path)}>
            <span class="made-icon" aria-hidden="true">{file.kind === "image" ? "▣" : file.kind === "video" ? "▶" : "◧"}</span>
            <span class="made-text">
              <span class="label-large">{file.name}</span>
              <span class="body-small muted">
                {file.kind === "image" ? "Image" : file.kind === "video" ? "Video" : "Design"} from the builder, opens in the canvas
              </span>
            </span>
          </button>
        {/each}
      {:else if row.t === "divider"}
        <div class="divider label-medium muted" role="separator"><span>{row.text}</span></div>
      {:else if row.t === "note"}
        <p class="note body-medium"><span class="label-medium muted">The builder's recap</span> {row.text}</p>
      {:else}
        <p class="command mono body-small muted">{row.text}</p>
      {/if}
    {/each}

    {#if notice}
      <p class="notice body-medium muted" role="status">{notice}</p>
    {/if}
    {#if latest && latest.skipped}
      <p class="body-small muted center">
        {latest.skipped} lines of the record were not understood and are left out.
      </p>
    {/if}

    {#if autopilot?.phase === "awaiting_approval"}
      <PlanCard status={autopilot} busy={controlBusy} onApprove={() => control("approve")} />
    {/if}

    {#each pending as request (request.id)}
      <div class="inline-request">
        <RequestCard
          {client}
          {request}
          {fetchedAt}
          {onAnswered}
          {offline}
          idPrefix="chat-req"
        />
      </div>
    {/each}
  </div>

  {#if !atFoot && unseen > 0}
    <button class="btn btn-tonal jump" onclick={jump}>
      {unseen} new {unseen === 1 ? "message" : "messages"}
    </button>
  {/if}

  {#if composer}
    <div class="composer" style:bottom={phone ? `${navPx}px` : undefined}>
      <label class="field">
        <span class="field-label">Message the builder</span>
        <textarea
          bind:value={draft}
          onkeydown={onComposerKey}
          rows="2"
          disabled={!composer.enabled}
          placeholder="Message the builder. It goes into the same session as the Terminal tab."
        ></textarea>
      </label>
      <div class="composer-row">
        <p class="body-small muted hint" role="status">{sendError ?? sentNote ?? composer.hint}</p>
        <button
          class="btn btn-filled"
          disabled={sending || !composer.enabled || draft.trim() === ""}
          onclick={send}
        >
          {sending ? "Sending…" : "Send"}
        </button>
      </div>
    </div>
  {/if}

  <p class="foot body-small muted">
    One conversation: what you type here goes into the session that is live in the
    <button type="button" class="link" onclick={onOpenTerminal}>Terminal tab</button>, and what you
    type there appears here.
  </p>
</section>

<details
  class="canvas-wrap"
  open={huge || canvasOpen}
  bind:this={canvasEl}
  ontoggle={(e) => (canvasOpen = (e.currentTarget as HTMLDetailsElement).open)}
>
  <summary class="rail-summary body-medium">Artifacts</summary>
  <ArtifactCanvas {client} {projectId} {active} {pinned} onOpenAll={onOpenArtifacts} />
</details>
</div>

<style>
  .layout {
    display: grid;
    gap: 1.5rem;
    max-width: 96rem;
  }

  .layout.with-rail {
    grid-template-columns: minmax(0, 1fr);
  }

  @media (min-width: 62rem) {
    .layout.with-rail {
      grid-template-columns: 17rem minmax(0, 1fr);
      align-items: start;
    }

    .rail-wrap > .rail-summary {
      display: none;
    }
  }

  /* The canvas is a third column on a wide screen; below that it folds under the thread. */
  @media (min-width: 84rem) {
    .layout {
      grid-template-columns: minmax(0, 1fr) 22rem;
      align-items: start;
    }

    .layout.with-rail {
      grid-template-columns: 17rem minmax(0, 1fr) 22rem;
    }

    .canvas-wrap > .rail-summary {
      display: none;
    }
  }

  @media (min-width: 62rem) and (max-width: 83.99rem) {
    .layout.with-rail > .canvas-wrap {
      grid-column: 2;
    }
  }

  .rail-wrap {
    padding: 0.25rem 0;
  }

  .quick {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-top: -0.75rem;
  }

  .rail-summary {
    display: flex;
    justify-content: space-between;
    gap: 0.75rem;
    min-height: 2.75rem;
    align-items: center;
    cursor: pointer;
    padding: 0 0.75rem;
    border-radius: var(--md-sys-shape-corner-medium, 12px);
    background: var(--md-sys-color-surface-container);
  }

  /* The plan strip (CUT-179): the step it is on and how far along, in one tappable row. */
  .rail-summary:has(.strip-top) {
    flex-direction: column;
    align-items: stretch;
    justify-content: center;
    gap: 0.35rem;
    padding: 0.5rem 0.75rem;
  }

  .strip-top {
    display: flex;
    justify-content: space-between;
    gap: 0.75rem;
    align-items: baseline;
  }

  .strip-title {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .strip-count {
    flex: none;
  }

  .strip-bar {
    display: block;
    height: 6px;
    border-radius: 3px;
    background: var(--md-sys-color-surface-container-highest);
    overflow: hidden;
  }

  .strip-fill {
    display: block;
    height: 100%;
    background: var(--md-sys-color-primary);
  }

  .rail-wrap > :global(.rail) {
    padding: 0.75rem 0.25rem 0;
  }

  .chat {
    position: relative;
    min-width: 0;
    max-width: 56rem;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    margin: 1rem 0;
  }

  .visually-hidden {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
  }

  .scroller {
    max-height: min(70vh, 44rem);
    min-height: 12rem;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 0.9rem;
    padding: 0.5rem 0.25rem 0.5rem 0;
  }

  .center {
    text-align: center;
    margin: 0;
  }

  .message {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
  }

  .message header {
    margin: 0;
  }

  .plain {
    margin: 0;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }

  .message.you .plain {
    padding: 0.75rem 1rem;
    border-radius: var(--md-sys-shape-corner-large, 16px);
    background: var(--md-sys-color-primary-container);
    color: var(--md-sys-color-on-primary-container);
  }

  .md {
    overflow-wrap: anywhere;
  }

  .tools {
    border: 1px solid var(--md-sys-color-outline-variant);
    border-radius: var(--md-sys-shape-corner-medium, 12px);
    background: var(--md-sys-color-surface-container-low);
  }

  .tools-head {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    width: 100%;
    min-height: 2.75rem;
    padding: 0 0.75rem;
    border: 0;
    background: transparent;
    color: var(--md-sys-color-on-surface);
    font: inherit;
    text-align: left;
    cursor: pointer;
    border-radius: inherit;
  }

  .tools-head:focus-visible {
    outline: 2px solid var(--md-sys-color-primary);
    outline-offset: -2px;
  }

  .chev {
    display: inline-flex;
    transition: transform 120ms;
  }

  .chev.open {
    transform: rotate(90deg);
  }

  .grow {
    flex: 1;
  }

  .failed {
    white-space: nowrap;
    padding: 0.1rem 0.5rem;
    border-radius: 0.75rem;
    background: var(--md-sys-color-error-container);
    color: var(--md-sys-color-on-error-container);
  }

  .calls {
    list-style: none;
    margin: 0;
    padding: 0 0.75rem 0.75rem;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  .call {
    margin: 0;
    font-size: 0.8125rem;
    overflow-wrap: anywhere;
  }

  .name {
    font-weight: 600;
    margin-right: 0.5rem;
  }

  .result {
    margin: 0.25rem 0 0;
    padding: 0.5rem 0.75rem;
    max-height: 12rem;
    overflow: auto;
    border-radius: var(--md-sys-shape-corner-small, 8px);
    background: var(--md-sys-color-surface-container);
    font-size: 0.75rem;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }

  .result.bad {
    background: var(--md-sys-color-error-container);
    color: var(--md-sys-color-on-error-container);
  }

  .divider {
    display: flex;
    align-items: center;
    gap: 0.75rem;
  }

  .divider::before,
  .divider::after {
    content: "";
    flex: 1;
    height: 1px;
    background: var(--md-sys-color-outline-variant);
  }

  .note {
    margin: 0;
    padding: 0.5rem 0.75rem;
    border-left: 3px solid var(--md-sys-color-outline-variant);
  }

  .command {
    margin: 0;
  }

  .notice {
    margin: 0;
  }

  .inline-request {
    margin-top: 0.25rem;
  }

  .jump {
    position: absolute;
    bottom: 3.25rem;
    left: 50%;
    transform: translateX(-50%);
  }

  .foot {
    margin: 0;
  }

  .composer {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  .composer :global(.field-label) {
    background: var(--md-sys-color-surface);
  }

  .composer-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    flex-wrap: wrap;
  }

  .hint {
    margin: 0;
    flex: 1 1 14rem;
  }

  .link {
    padding: 0;
    border: 0;
    background: none;
    color: var(--md-sys-color-primary);
    font: inherit;
    text-decoration: underline;
    cursor: pointer;
    min-height: 2.75rem;
  }

  /* A file the builder made, as a card in the thread. */
  .made {
    display: flex;
    gap: 0.75rem;
    align-items: center;
    width: 100%;
    min-height: 3.5rem;
    padding: 0.6rem 0.9rem;
    border: 1px solid var(--md-sys-color-outline-variant);
    border-radius: var(--md-sys-shape-corner-large, 16px);
    background: var(--md-sys-color-surface-container-lowest);
    color: var(--md-sys-color-on-surface);
    font: inherit;
    text-align: left;
    cursor: pointer;
  }

  .made:focus-visible {
    outline: 2px solid var(--md-sys-color-primary);
    outline-offset: 2px;
  }

  .made-icon {
    font-size: 1.5rem;
    color: var(--md-sys-color-primary);
  }

  .made-text {
    display: flex;
    flex-direction: column;
    min-width: 0;
    overflow-wrap: anywhere;
  }

  @media (max-width: 640px) {
    /* One scroll area: the page's. The thread is as tall as it is, and the box stays in reach. */
    .scroller {
      max-height: none;
      overflow-y: visible;
    }

    .composer {
      position: sticky;
      z-index: 5;
      padding: 0.5rem 0 0.5rem;
      background: var(--md-sys-color-surface);
      border-top: 1px solid var(--md-sys-color-outline-variant);
    }
  }
</style>
