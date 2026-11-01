# Scale-testing the dashboard with a seeded fleet

Every screen was built for two or three projects. `scripts/seed_fleet.py` registers 100 fake
ones so you can see what the Overview does with a portfolio (CUT-187, epic E22).

```bash
export HOME_SCRATCH=$(mktemp -d)            # brand new every time: never reuse one
uv run python scripts/seed_fleet.py seed  --home $HOME_SCRATCH           # 100 projects
uv run python scripts/seed_fleet.py clear --home $HOME_SCRATCH           # removes only seed-* ones
```

`--count N` seeds a different number. The script refuses your real home directory.

What it writes: rows in `<home>/.cuttlefish/projects.db` named `seed-001-billing` and so on, and a
small episodic journal in each project's own folder under `<home>/seed-projects/`. Of every 100:
30 working (last event 1 to 20 minutes ago), 15 waiting (a round finished and a review is
wanted), 30 done, 15 stuck (half failed, half silent for hours) and 10 idle (never run).

Eight in nine carry tags (`client:acme`, `stack:rust`, and `priority` on some) to try the filter bar.

What it does not do: launch an agent, call a model or touch a tmux socket. The projects have no
persisted roles, so a daemon's resume skips them, and nothing runs until you press Start on one.
Journal times are relative to the moment you seed, so a "working" project turns "stuck" after the
stuck threshold passes; seed again for a fresh set.

## What 100 projects cost (CUT-192, 2026-11-02)

`scripts/measure_dashboard.mjs` times the Overview against a seeded fleet on Lighthouse's mobile
profile (390 px, 4x CPU slowdown, 1.6 Mbps, 150 ms round trip) and exits 1 when the first project card
of a returning visit takes more than 2 s. It needs Playwright and a Chromium, so it is a by-hand
check, not part of `make ci`; the parts that can be counted are guarded by unit tests
(`test_project_list_budget.py`, `pixelimage.test.ts`).

| Median of 5 to 7 runs | Before | After |
|---|---|---|
| First card, first visit (connect, then list) | 9.0 s | 2.2 s |
| All 100 cards, first visit | 9.5 s | 2.6 s |
| First card, returning visit | not measured | 1.5 s |
| All 100 cards, returning visit | not measured | 1.9 s |
| Main thread busy per 10 s of polling | 8.3 s | 0.8 s |
| DOM nodes | 20,028 | 3,233 |
| `GET /api/projects` | 216 KB, 280 ms | 107 KB, about 100 ms |

The worst cost was the sprites: each pixel of each cuttlefish was its own `<span>`, so 200 sprites
were 17,000 nodes, and the 37 working roles re-rendered every pixel twice a second. Now a sprite is one
element with a cached SVG as its background (`pixel/pixelimage.ts`). Next, in order of what each
saved: cards below the screen skip layout and paint (`content-visibility: auto`), the list leaves out
role persona text (59% of the payload; the project's own route still has it) and reads each journal
once instead of three times, and the list draws 20 cards at a time.

Not fixed: the connect screen checks the token with a full list fetch and the Overview then fetches
it again, which is about 0.9 s on this profile on a first visit.

To look at it: `HOME=$HOME_SCRATCH CUTTLEFISH_SECRETS_KEY=$(uv run cuttlefish secrets generate-key)
uv run cuttlefish serve --port <free port>`, from a directory outside the repo.
