---
icon: lucide/map
---

# Tutorial: two projects, one dashboard


About ten minutes. This is the long version; the [quickstart](quickstart.md) is the short one. Agents spend your model credit, usually cents for the tasks
below.

## 1. Install and make two practice repos


```bash
curl -fsSL https://raw.githubusercontent.com/leejianrong/cuttlefish-crew/main/scripts/install.sh | sh
```

The script (read it first: it is short and commented) installs uv if missing, clones this repo into
`~/.local/share/cuttlefish/app`, puts `cuttlefish` in `~/.local/bin`, builds the dashboard, and runs
`cuttlefish doctor`. No sudo. It names anything it cannot install (Node 20.19+/22.12+, tmux, an agent CLI) and
changes nothing else; run it again to update. `sh install.sh --check` only checks, `--uninstall` removes it
(ADR-0045). Prefer a checkout to hack on? `git clone`, `uv sync`, `make demo` still work.

```bash
mkdir -p ~/crew-demo && cd ~/crew-demo
for p in todo-api notes-site; do mkdir $p && git -C $p init -q -b main; printf '.cuttlefish/\n.satay/\n' > $p/.gitignore; done
printf 'class TodoList:\n    def __init__(self):\n        self.items = []\n\n    def add(self, item):\n        self.items.append(item)\n' > todo-api/todo.py
printf '<!doctype html>\n<title>Notes</title>\n<h1>Notes</h1>\n' > notes-site/index.html
for p in todo-api notes-site; do git -C $p add -A && git -C $p -c user.name=demo -c user.email=demo@example.com commit -qm init; done
cd -
```

## 2. Start the dashboard


```bash
export OPENROUTER_API_KEY=...        # or the key your agent CLI uses
cuttlefish serve                     # installed with the script; from a checkout: make demo
```

`make demo` builds the dashboard once; either way `cuttlefish serve` is one process
serving the API and the UI. It prints a URL and a token. Open the URL, check
that the address box on the Connect screen matches it (it starts as the address
the page was served from), and paste the token. The token is kept in
`~/.cuttlefish/serve-token` (readable only by you), so restarting the daemon
does not sign an open dashboard out; delete the file to get a new one. No daemon yet? The Connect
screen also links to a sprite gallery that needs no connection. The daemon's
log prints in this terminal and is always written to
`~/.cuttlefish/logs/cuttlefish.log`; `make demo LOG=1` also copies it to
`/tmp/cuttlefish.log`. Ctrl-C stops it cleanly.

## 3. Add both projects
 On Projects choose **Add project**, pick
`~/crew-demo/todo-api` in the folder picker, keep the Builder + reviewer team,
and add it. Repeat for `~/crew-demo/notes-site` with the Solo builder team. (From
a terminal, `uv run cuttlefish projects add --name todo-api --root ~/crew-demo/todo-api`
does the same; add `--max-tokens 200000` to cap a project's token use.)

## 4. Start both crews
 Open **todo-api**. These two demo projects use kopicode, whose
interactive session cannot take a first message yet, so use **Advanced: run a team of roles** on the
Overview, give the builder a task, and press **Start team**. (For a project you want to work on with
Claude Code or Codex, the main box is **Give the builder a goal**: it starts that agent in its own
terminal with your goal as its first message. Switch on **Its own branch** there and each goal runs in a
new git worktree and branch, leaving your checkout alone: [ADR-0048](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/adr/0048-each-goal-can-run-in-its-own-git-worktree-and-branch-inside-the-project-root.md).
**Open a pull request** under the goal runs `gh pr create` with your own `gh` login and never merges:
[ADR-0049](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/adr/0049-a-goal-branch-opens-a-github-pull-request-through-the-machines-own-gh-and-never-merges.md).)

> Add a remove(item) method to TodoList in todo.py, and a test for it. Then run
> `uname -a` and tell me what it prints.

Go back to Projects, open **notes-site**, and start its builder on:

> Add a short About section to index.html.

The two teams run side by side; each agent is a small sprite whose pose is its
status. Roles on kopicode in a git project each work in their own
worktree and run together; in a folder that is not a git repository they run one at a time, because kopicode locks a
working tree per session.

## 5. Answer what needs you
 The bell in the rail and the **Needs you** tab show
a count. A command that is not on the project's list pauses that agent until you
choose **Allow once**, **Always allow** (the start of the command, saved to the
project's commands), or **Deny**. Left alone, it denies itself after ten minutes. With
kopicode v0.4.0 or later, a question the agent asks you shows up here too, with a box for your answer.
You can answer from the project's Overview as well, without switching tabs. A line
that chains commands (`a && b`) is decided command by command, and Always allow
offers a rule for the first command that is not on the list.

How much asks you is the project's permission mode: **Ask first** (every shell
command waits for you), **Standard** (the default: a list of safe commands runs,
others ask) or **Auto** (anything but the never-allowed list, which is `sudo`, a
forced `git push`, a download piped into a shell and writes outside the project
root, in every mode). A role can also be read-only. A mode applies the next time
the team starts; details per backend are in the
[CLI reference](cli-reference.md#permission-modes).

## 6. Steer, review, stop
 While a role is running you can send it a
redirect; a Codex role takes it at once, any other role at the next round boundary. Turn on **Approve
each round before it ends** before starting to review a round first. **Stop team**
asks the agent to finish its current step and stop. When it is done, see what the
crew did:

```bash
git -C ~/crew-demo/todo-api diff
```

The Activity log on each project, and `uv run cuttlefish show <team-id>`, replay
every step from the journal.

## 7. Keep an agent running
 The **Agent session** card on a project's Overview starts the
project's agent (`claude`, `codex` or `kopicode`, the project's backend) in tmux. Close the browser or
restart `cuttlefish serve` and it is still there; the card says it was **reattached**. After a reboot
tmux is gone, and the next `cuttlefish serve` starts the agent again with the tool's own resume
command (**relaunched**), so the conversation carries on. A Claude Code session asks for permission
under **Needs you** as well as in its own terminal (answer in either); Codex and kopicode ask in the terminal only.
Open the project's **Terminal** tab, which is the real tmux session in
the browser (xterm.js, dark by default in both app themes, with a Match app / Dark / Light switch remembered per browser), with a key row for a phone that sits right under the terminal, above the bottom bar. The **Shell** switch beside it opens a plain shell in the project folder (no agent needed, the agents' filtered environment, no project secrets). It needs the same
sign-in as the rest of the dashboard, and closing it leaves the agent running. The page title shows the
Needs you count, and if the daemon restarts under an open page it asks you to sign in again.
Any other terminal agent works too: `cuttlefish projects add --terminal-command 'aider'` (or `PATCH
/api/projects/<id>/terminal-command`) makes the project's tmux session run that command instead. The Terminal tab
attaches to it, but cuttlefish reads nothing back: the page says it has **no live status, no chat, no goal and no
Needs you cards** for it, and a session that exits is run again from the start, not resumed
([ADR-0047](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/adr/0047-a-project-can-run-any-terminal-command-in-its-tmux-session-with-no-status-integration.md)).

## 8. Read what an agent made
 Agents are told to write a result (a plan, a review, a summary) as a
markdown file under `.cuttlefish/artifacts/` in the project. The project's **Artifacts** tab lists those
files, newest first, and shows markdown and images (png, jpg, gif, webp, svg); it reads the folder again every
few seconds, so a new file appears without a refresh. Other files are listed by name only. Markdown is
rendered by the dashboard itself, with no raw HTML, so nothing an agent writes there runs. Every round's task
carries that instruction, and so does a Claude Code session started in tmux (`--append-system-prompt`). A
Codex or kopicode session in tmux is not told yet, so ask it in your first message.

