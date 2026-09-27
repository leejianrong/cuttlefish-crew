# Launch channel plan (KAN-1720)

Timed deliberately, per the card's own instruction: the landing page (KAN-1717)
and a genuinely demoable remote-access story (Slice E, complete) both exist
now, so this isn't premature. Nothing here gets posted by this session -
posting is Jian's own call, on his own accounts, whenever the timing and
final wording feel right to him. What follows is the actual next step the
card asked for: three real channels, and a real draft for each, not a
generic "do customer interviews" placeholder.

## Channels, and why these three

1. **Show HN.** The primary channel. HN's own audience is exactly the
   validated persona (`docs/gtm/personas.md` P1): a solo developer running
   several coding-agent sessions who'd try a fleet manager themselves,
   not just read about one. HN also rewards the honest-gaps discipline
   this whole project already has, over polished claims.
2. **r/ClaudeAI.** A narrower, already-self-selected audience: people
   already running headless Claude Code sessions day to day, who feel the
   exact pain (juggling terminal tabs across projects) this tool answers
   most directly. Worth posting a day or two after HN, not the same day,
   so it reads as its own post rather than cross-posted spam.
3. **A short thread on X.** Lower effort, wider casual reach, and the
   natural place for the demo video KAN-1717 already produced to actually
   get watched (X's own video player autoplays inline; HN and Reddit don't).

Not picked, on purpose: r/programming and r/selfhosted are real
possibilities but wider and less targeted than the two above - a second
wave if the first round lands well, not part of this first push.

## Sequencing

Post Show HN first, on a weekday morning (HN's own traffic curve). Post
the X thread the same day, linking back to the HN post once it's live
(HN traffic is the bigger draw; the thread should point at it, not
compete with it). Hold r/ClaudeAI for a day or two later so it reads as
its own post.

## Show HN

**Title:** `Show HN: Cuttlefish-crew – a fleet manager for teams of coding agents`

**Body:**

> I kept losing track of which coding agent was doing what, in which
> terminal tab, across which of my own side projects. Cuttlefish-crew is
> what I built instead: register a project, name a small team of agents
> (kopicode, headless Claude Code, or headless Codex - your choice per
> project), and watch and steer all of them from one dashboard.
>
> The core loop is a durable workflow, built on satay-runtime (a project
> of mine), so killing the process mid-task and restarting it picks up
> exactly where it left off rather than losing the run. Each role in a
> project's crew shows up as a small pixel-art sprite whose colour
> changes with its status: queued, working, blocked and waiting on a
> decision from you, done, or failed. It's not trying to be an org chart
> with hiring workflows and SSO - it's closer to three or four named
> agents you actually talk to, per project.
>
> It's self-hosted (a `cuttlefish serve` you run yourself, reachable from
> another device over Tailscale), and it's entirely built by Claude Code
> under my own review, one PR per slice, which is its own kind of proof
> this whole approach holds up on a real codebase, not just a toy one.
>
> Repo and demo: <landing page URL>. Docs: <docs URL>. I'd like to hear
> what breaks for you, especially if you try mixing in your own project
> layout rather than a fresh checkout.

## r/ClaudeAI

**Title:** `Built a fleet manager for running several Claude Code sessions across many projects at once`

**Body:**

> If you're running headless Claude Code across more than one project,
> you've probably felt this: N terminal tabs, no shared view of what's
> actually happening in any of them, and no way to tell "still working"
> from "silently stuck" without switching windows and reading scrollback.
>
> I built cuttlefish-crew to fix that for myself. Register a project,
> name a few roles (a builder, a reviewer, whatever shape fits), point
> each one at headless Claude Code (kopicode and Codex both work too,
> picked per project), and run them concurrently from one dashboard.
> Approve or reject a round before it finalises if you want a review
> gate rather than a fire-and-forget task. The whole thing survives a
> crash: it resumes the exact run it was on, not a fresh one.
>
> Apache-2.0, self-hosted, no account or cloud service needed. Repo and a
> short demo here: <landing page URL>.

## X thread

1/ Cuttlefish-crew: a fleet manager for teams of coding agents. Register
your projects, name a small crew per one (kopicode, headless Claude
Code, or Codex), and run and steer all of it from one dashboard.
<demo video or GIF>

2/ Kill the process mid-task and restart it: it resumes the exact run,
not a fresh one. The whole core loop is a durable workflow, not an
ordinary script wrapped in retries afterwards.

3/ Every role shows up as a small pixel-art sprite whose colour changes
with its status (queued, working, blocked, done, failed) - a cuttlefish
changes colour to say what it's doing, so does every agent in your crew.

4/ Self-hosted, Apache-2.0, and built entirely by Claude Code under my
own review, one PR per slice. Try it: <landing page URL>

## What this deliberately isn't

No paid ads, no influencer outreach, no press list. A solo project's
first launch is these three posts and whatever organic reshares follow -
matching the "crew, not company" positioning (`docs/gtm/positioning.md`):
earn attention the same way the product earns trust, by being honest
about what it does and doesn't do yet, not by buying reach.
