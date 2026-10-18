<script lang="ts">
  import qrcode from "qrcode-generator";
  import {
    FleetApiError,
    type DeviceInfo,
    type DeviceTier,
    type DevicesState,
    type FleetClient,
    type RelayInfo,
  } from "../api";
  import { formatCode, pairingAddressProblem } from "../relay";

  let { client }: { client: FleetClient } = $props();

  const TIERS: { id: DeviceTier; label: string; note: string }[] = [
    { id: "view", label: "View", note: "Read only." },
    { id: "approve", label: "Approve", note: "Answer Needs you, steer, start and stop. The default." },
    {
      id: "terminal",
      label: "Terminal",
      note: "Also the agent's terminal, which types commands as you.",
    },
    { id: "admin", label: "Admin", note: "Also settings, secrets, projects and devices." },
  ];

  const ADDRESS_KEY = "cuttlefish.pairing.address";

  let relay = $state<RelayInfo | null>(null);
  let roster = $state<DevicesState | null>(null);
  let failed = $state(false);
  let notice = $state<string | null>(null);
  let working = $state(false);
  let now = $state(Date.now() / 1000);
  let link = $state<string | null>(null);
  let removing = $state<string | null>(null);

  function savedAddress(): string {
    try {
      return localStorage.getItem(ADDRESS_KEY) || window.location.origin;
    } catch {
      return window.location.origin;
    }
  }
  let address = $state(savedAddress());
  // The daemon may know the https page a phone should open; offer it until the person types their own,
  // over whatever the PC's own browser is on or an earlier address left in storage.
  let addressTouched = $state(false);

  const you = $derived(relay?.you ?? null);
  // The owner's own session is not held to a tier; a paired device is, and the daemon says no
  // to what it may not do. These only decide what to show.
  const canAdmin = $derived(you === null || you.tier === "admin");
  const addressProblem = $derived(pairingAddressProblem(address));
  const active = $derived((roster?.devices ?? []).filter((d) => d.revoked_at === null));
  const removed = $derived((roster?.devices ?? []).filter((d) => d.revoked_at !== null));
  const secondsLeft = $derived(
    roster?.pairing ? Math.max(0, Math.round(roster.pairing.expires_at - now)) : 0,
  );
  const qr = $derived.by(() => {
    if (!link) return "";
    const code = qrcode(0, "M");
    code.addData(link);
    code.make();
    return code.createSvgTag({ scalable: true, margin: 2 });
  });

  async function load() {
    try {
      relay = await client.getRelay();
      if (!addressTouched && relay.page_url && address !== relay.page_url) address = relay.page_url;
      if (relay.enabled) roster = await client.listDevices();
      failed = false;
    } catch {
      failed = true;
    }
  }

  $effect(() => {
    load();
    const poll = setInterval(load, 2500);
    const tick = setInterval(() => (now = Date.now() / 1000), 1000);
    return () => {
      clearInterval(poll);
      clearInterval(tick);
    };
  });

  // The link is only good while its pairing is: when the daemon says there is none, drop it.
  $effect(() => {
    if (roster && !roster.pairing) link = null;
  });

  async function act(work: () => Promise<unknown>, done?: string) {
    working = true;
    notice = null;
    try {
      await work();
      if (done) notice = done;
    } catch (error) {
      notice = error instanceof FleetApiError || error instanceof Error ? error.message : "That did not work.";
    } finally {
      working = false;
      await load();
    }
  }

  function startPairing() {
    return act(async () => {
      const made = await client.startPairing();
      try {
        localStorage.setItem(ADDRESS_KEY, address);
      } catch {
        /* a convenience only */
      }
      link = `${address.replace(/\/+$/, "")}/#pair=${made.payload}`;
    });
  }

  async function copy() {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link);
      notice = "Link copied.";
    } catch {
      notice = "Could not copy. Select the link and copy it by hand.";
    }
  }

  const tierNote = (tier: DeviceTier) => TIERS.find((t) => t.id === tier)?.note ?? "";
  const seen = (d: DeviceInfo) => (d.last_seen_at ? ago(d.last_seen_at) : "not used yet");

  function ago(epoch: number): string {
    const seconds = Math.max(0, Math.round(now - epoch));
    if (seconds < 90) return "just now";
    if (seconds < 5400) return `${Math.round(seconds / 60)} min ago`;
    if (seconds < 129600) return `${Math.round(seconds / 3600)} h ago`;
    return `${Math.round(seconds / 86400)} days ago`;
  }
</script>

<div class="page">
  <header>
    <h1 class="headline-small">Devices</h1>
    <p class="body-medium muted">
      Phones and other browsers paired with this PC through the relay. Each has its own key and
      secret, and the daemon holds each to what you allow.
    </p>
  </header>

  {#if failed && !relay}
    <p class="status" role="status">Lost connection to the daemon. Retrying…</p>
  {:else if relay && !relay.enabled}
    <section class="card">
      <h2 class="title-medium">The relay is not on</h2>
      <p class="muted">
        Start the daemon with a relay to pair devices:
        <code>cuttlefish serve --relay ws://127.0.0.1:8450</code>, after
        <code>cuttlefish relay</code> in another terminal. Phones on Tailscale do not need it.
      </p>
    </section>
  {:else if relay && you && !roster}
    <!-- A device below Approve may not list devices, so there is no roster; it can still be told what it is. -->
    <section class="card">
      <h2 class="title-medium">This browser</h2>
      <p class="status" role="status">This browser is a paired device ({you.name}, {you.tier}).</p>
      <p class="muted">{tierNote(you.tier)}</p>
      <p class="muted">
        The device list needs Approve, and pairing others or changing tiers needs Admin. You give a
        device more on the PC.
      </p>
    </section>
  {:else if relay && roster}
    {#if you}
      <p class="status" role="status">
        This browser is a paired device ({you.name}, {you.tier}).
        {#if !canAdmin}Pairing devices and changing tiers need Admin, which you give on the PC.{/if}
      </p>
    {/if}
    {#if notice}<p class="status" role="status">{notice}</p>{/if}

    {#if canAdmin}
      <section class="card">
        <h2 class="title-medium">Pair a device</h2>
        {#if !link}
          <p class="muted">
            Makes a link and a QR code that work once, for five minutes. The device still needs
            your confirmation here.
          </p>
          <label>
            Address the device will open
            <input type="url" bind:value={address} oninput={() => (addressTouched = true)} placeholder="http://100.x.y.z:8431" />
          </label>
          {#if addressProblem}
            <p class="warn">{addressProblem}</p>
          {/if}
          <div class="row">
            <button class="btn btn-filled" disabled={working} onclick={startPairing}>
              Make a pairing link
            </button>
          </div>
        {:else}
          <div class="pairing">
            <div class="qr" aria-label="QR code for the pairing link">{@html qr}</div>
            <div class="grow">
              <p>
                Scan this with the phone, or open the link on it. It works once and expires in
                <strong>{Math.floor(secondsLeft / 60)}:{String(secondsLeft % 60).padStart(2, "0")}</strong>.
              </p>
              <p class="link mono">{link}</p>
              <p class="warn">
                Anyone who opens it before you do can ask to pair, but nobody is let in until you
                confirm below and the codes match.
              </p>
              <div class="row">
                <button class="btn btn-tonal" onclick={copy}>Copy link</button>
                <button class="btn btn-text" disabled={working} onclick={() => act(() => client.cancelPairing())}>
                  Cancel
                </button>
              </div>
            </div>
          </div>
        {/if}
      </section>
    {/if}

    {#if roster.pending.length > 0}
      <section class="card attention">
        <h2 class="title-medium">Waiting for you</h2>
        {#each roster.pending as pending (pending.id)}
          <div class="pending">
            <div class="grow">
              <p class="name">{pending.name}</p>
              <p class="muted">
                Confirm only if the phone shows the same code:
              </p>
              <p class="code mono" aria-label="Code {pending.code.split('').join(' ')}">
                {formatCode(pending.code)}
              </p>
            </div>
            {#if canAdmin}
              <div class="row">
                <button class="btn btn-filled" disabled={working} onclick={() => act(() => client.confirmDevice(pending.id), `${pending.name} is paired.`)}>
                  Confirm
                </button>
                <button class="btn btn-text" disabled={working} onclick={() => act(() => client.rejectDevice(pending.id), "Turned it down.")}>
                  Reject
                </button>
              </div>
            {/if}
          </div>
        {/each}
      </section>
    {/if}

    <section class="card">
      <h2 class="title-medium">Paired devices</h2>
      {#if active.length === 0}
        <p class="muted">None yet.</p>
      {/if}
      {#each active as device (device.id)}
        <div class="device">
          <div class="grow">
            <p class="name">{device.name}{#if you && you.id === device.id} <span class="tag">this browser</span>{/if}</p>
            <p class="muted">Last used {seen(device)}</p>
          </div>
          <label class="tier">
            Allowed to
            <select
              value={device.tier}
              disabled={working || !canAdmin}
              onchange={(event) =>
                act(() => client.setDeviceTier(device.id, (event.currentTarget as HTMLSelectElement).value as DeviceTier))}
            >
              {#each TIERS as tier (tier.id)}
                <option value={tier.id}>{tier.label}</option>
              {/each}
            </select>
          </label>
          {#if removing === device.id}
            <div class="row">
              <span class="warn">Sign {device.name} out at once?</span>
              <button class="btn btn-danger" disabled={working} onclick={() => { removing = null; void act(() => client.revokeDevice(device.id), `${device.name} was removed.`); }}>
                Remove
              </button>
              <button class="btn btn-text" onclick={() => (removing = null)}>Keep</button>
            </div>
          {:else}
            <button class="btn btn-danger-text" onclick={() => (removing = device.id)}>Remove</button>
          {/if}
          <p class="tiernote muted">{tierNote(device.tier)}</p>
        </div>
      {/each}
    </section>

    {#if removed.length > 0}
      <section class="card">
        <h2 class="title-medium">Removed</h2>
        {#each removed as device (device.id)}
          <p class="muted">{device.name}, removed {ago(device.revoked_at ?? 0)}</p>
        {/each}
      </section>
    {/if}
  {:else}
    <p class="muted">Loading…</p>
  {/if}
</div>

<style>
  .page {
    max-width: 52rem;
    margin: 0 auto;
    padding: 2rem 1.5rem 4rem;
    display: flex;
    flex-direction: column;
    gap: 1rem;
  }

  h1,
  h2,
  p {
    margin: 0;
  }

  .muted {
    color: var(--text-muted);
  }

  .card {
    padding: 1.25rem;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }

  .card.attention {
    border-color: var(--md-sys-color-attention);
  }

  .status {
    padding: 10px 16px;
    border-radius: var(--md-sys-shape-corner-small);
    background: var(--md-sys-color-secondary-container);
    color: var(--md-sys-color-on-secondary-container);
  }

  .warn {
    font-size: 0.85rem;
    color: var(--md-sys-color-on-surface-variant);
  }

  .row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
  }

  .grow {
    flex: 1;
    min-width: 12rem;
  }

  .pairing {
    display: flex;
    flex-wrap: wrap;
    gap: 1.25rem;
  }

  .qr {
    width: 12rem;
    max-width: 100%;
    padding: 0.5rem;
    background: #fff;
    border-radius: var(--md-sys-shape-corner-small);
    align-self: flex-start;
  }

  .qr :global(svg) {
    display: block;
    width: 100%;
    height: auto;
  }

  .link {
    word-break: break-all;
    font-size: 0.75rem;
    padding: 0.5rem 0.7rem;
    border: 1px solid var(--md-sys-color-outline-variant);
    border-radius: var(--md-sys-shape-corner-extra-small);
    margin: 0.5rem 0;
    max-height: 5rem;
    overflow: auto;
  }

  .pending,
  .device {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 12px;
    padding: 0.75rem 0;
    border-top: 1px solid var(--md-sys-color-outline-variant);
  }

  .pending:first-of-type,
  .device:first-of-type {
    border-top: 0;
  }

  .name {
    font-weight: 600;
  }

  .code {
    font-size: 2rem;
    font-weight: 700;
    letter-spacing: 0.12em;
  }

  .tag {
    font-size: 0.7rem;
    font-weight: 500;
    padding: 2px 8px;
    border-radius: 8px;
    background: var(--md-sys-color-secondary-container);
    color: var(--md-sys-color-on-secondary-container);
  }

  .tier {
    font-size: 0.8rem;
    color: var(--text-muted);
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  select,
  input {
    padding: 0.45rem 0.6rem;
    background: transparent;
    color: var(--text);
    border: 1px solid var(--md-sys-color-outline);
    border-radius: var(--md-sys-shape-corner-extra-small);
  }

  label {
    display: block;
    font-size: 0.82rem;
    color: var(--text-muted);
  }

  label input {
    display: block;
    width: 100%;
    margin-top: 0.35rem;
  }

  .tiernote {
    width: 100%;
    font-size: 0.8rem;
  }

  .mono {
    font-family: var(--md-ref-typeface-mono);
  }
</style>
