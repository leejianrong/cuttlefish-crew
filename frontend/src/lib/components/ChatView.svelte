<script lang="ts">
  // The Chat tab (CUT-172, ADR-0044): the agent's conversation, read from its own session record
  // (Claude Code's transcript) and never stored a second time. Read-only here; typing into the
  // session is CUT-173, so the notice at the foot says where to type until then. A permission
  // the builder is waiting on is answered inline, with the same card as Needs you.
  import type { FleetClient, NeedsYouRequest } from "../api";
  import {
    EMPTY_CHAT,
    applyChat,
    chatNotice,
    clockTime,
    toRows,
    type ChatResponse,
    type ChatState,
  } from "../chat";
  import { parseMarkdown } from "../markdown";
  import Icon from "./Icon.svelte";
  import Markdown from "./Markdown.svelte";
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
  }: {
    client: FleetClient;
    projectId: string;
    active: boolean;
    pending: NeedsYouRequest[];
    fetchedAt: number;
    onAnswered: () => void;
    offline: boolean;
    onOpenTerminal: () => void;
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

  function nearFoot(): boolean {
    if (!scroller) return true;
    return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 80;
  }

  function jump() {
    if (!scroller) return;
    scroller.scrollTop = scroller.scrollHeight;
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

  function toggle(id: string) {
    opened[id] = !opened[id];
  }
</script>

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

  <p class="foot body-small muted">
    This is a view of the conversation. To say something to the builder, type in the
    <button type="button" class="link" onclick={onOpenTerminal}>Terminal tab</button>; it is the same
    session.
  </p>
</section>

<style>
  .chat {
    position: relative;
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

  @media (max-width: 640px) {
    .scroller {
      max-height: 60dvh;
    }
  }
</style>
