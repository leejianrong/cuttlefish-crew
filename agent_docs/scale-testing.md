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

To look at it: `HOME=$HOME_SCRATCH CUTTLEFISH_SECRETS_KEY=$(uv run cuttlefish secrets generate-key)
uv run cuttlefish serve --port <free port>`, from a directory outside the repo.
