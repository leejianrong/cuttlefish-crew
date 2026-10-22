// The chat thread's pure parts (CUT-172): applying a read to what the page holds, and turning the
// items into the rows it draws. The daemon reads the agent's own session record (ADR-0044); nothing
// here touches the network or the DOM, so each rule is unit-tested.

export interface ChatBase {
  /** The transcript line's id plus the block's place in it; null only for a record with no ids. */
  id: string | null;
  at: string | null;
}

export type ChatItem =
  | (ChatBase & { kind: "user" | "assistant" | "note" | "divider" | "command"; text: string })
  | (ChatBase & { kind: "tool"; tool_id: string; name: string; summary: string })
  | (ChatBase & {
      kind: "tool_result";
      tool_id: string;
      text: string;
      is_error: boolean;
    });

export interface ChatResponse {
  backend: string | null;
  session: "running" | "stopped" | "none";
  session_id: string | null;
  /** Whether this backend's record can be read yet (Claude Code only, for now). */
  supported: boolean;
  items: ChatItem[];
  cursor: number;
  /** The session is up but its record file is not there yet. */
  waiting?: boolean;
  /** Messages before the first ones shown are in the file, not here. */
  earlier?: boolean;
  /** The file got shorter: drop what the page holds and take this read as the whole. */
  reset?: boolean;
  /** Lines of the record that were not understood. */
  skipped?: number;
  /** What the session is doing, from its hooks and its screen (Claude Code only). */
  activity?: { state: "idle" | "busy" | "dialog" | "unknown" | "none"; seconds: number | null };
  path?: string;
}

export interface ChatState {
  items: ChatItem[];
  /** Ask for this next; null until the first read. */
  cursor: number | null;
}

export const EMPTY_CHAT: ChatState = { items: [], cursor: null };

/** What the page holds after `response`. A reset replaces it; otherwise new items are appended,
 * and one already held (same id) is not shown twice. */
export function applyChat(state: ChatState, response: ChatResponse): ChatState {
  if (!response.supported || response.waiting) return state.cursor === null ? EMPTY_CHAT : state;
  const base = response.reset ? [] : state.items;
  const seen = new Set(base.map((item) => item.id).filter((id): id is string => id !== null));
  const added = response.items.filter((item) => item.id === null || !seen.has(item.id));
  return { items: added.length === 0 && !response.reset ? state.items : [...base, ...added], cursor: response.cursor };
}

export interface ToolCall {
  toolId: string;
  name: string;
  summary: string;
  result: { text: string; isError: boolean } | null;
}

export type Row =
  | { t: "message"; who: "you" | "builder"; id: string; at: string | null; text: string }
  | { t: "tools"; id: string; calls: ToolCall[]; at: string | null; label: string; failed: number }
  | { t: "divider" | "note" | "command"; id: string; at: string | null; text: string };

const EDIT_TOOLS = new Set(["Edit", "Write", "MultiEdit", "NotebookEdit"]);
const READ_TOOLS = new Set(["Read"]);
const SEARCH_TOOLS = new Set(["Grep", "Glob", "LS"]);
const DELEGATE_TOOLS = new Set(["Task", "Agent"]);
const WEB_TOOLS = new Set(["WebFetch", "WebSearch"]);

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

/** "Ran 38 commands, edited 9 files": what a run of tool calls did, in a line. */
export function toolLabel(calls: readonly ToolCall[]): string {
  let commands = 0;
  let reads = 0;
  let searches = 0;
  let delegated = 0;
  let lookups = 0;
  let others = 0;
  const edited = new Set<string>();
  for (const call of calls) {
    if (call.name === "Bash") commands++;
    else if (EDIT_TOOLS.has(call.name)) edited.add(call.summary || call.toolId);
    else if (READ_TOOLS.has(call.name)) reads++;
    else if (SEARCH_TOOLS.has(call.name)) searches++;
    else if (DELEGATE_TOOLS.has(call.name)) delegated++;
    else if (WEB_TOOLS.has(call.name)) lookups++;
    else others++;
  }
  const parts: string[] = [];
  if (commands) parts.push(`ran ${plural(commands, "command", "commands")}`);
  if (edited.size) parts.push(`edited ${plural(edited.size, "file", "files")}`);
  if (reads) parts.push(`read ${plural(reads, "file", "files")}`);
  if (searches) parts.push(`searched ${plural(searches, "time", "times")}`);
  if (delegated) parts.push(`handed off ${plural(delegated, "task", "tasks")}`);
  if (lookups) parts.push(`looked up ${plural(lookups, "page", "pages")}`);
  if (others) parts.push(`used ${plural(others, "other tool", "other tools")}`);
  const text = parts.join(", ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** The thread as rows: messages as they came, and each run of tool calls folded into one row with
 * its results attached to their calls. */
export function toRows(items: readonly ChatItem[]): Row[] {
  const rows: Row[] = [];
  const byTool = new Map<string, ToolCall>();
  let group: Extract<Row, { t: "tools" }> | null = null;
  const place = (at: string | null) => {
    if (!group) {
      group = { t: "tools", id: `tools-${rows.length}`, calls: [], at, label: "", failed: 0 };
      rows.push(group);
    }
    return group;
  };
  items.forEach((item, index) => {
    const id = item.id ?? `${item.kind}-${index}`;
    if (item.kind === "tool") {
      const call: ToolCall = { toolId: item.tool_id, name: item.name, summary: item.summary, result: null };
      byTool.set(item.tool_id, call);
      place(item.at).calls.push(call);
      return;
    }
    if (item.kind === "tool_result") {
      const known = byTool.get(item.tool_id);
      const result = { text: item.text, isError: item.is_error };
      if (known) known.result = result;
      else {
        // Its call is before what was read: show the result on its own.
        const orphan: ToolCall = { toolId: item.tool_id, name: "tool", summary: "", result };
        byTool.set(item.tool_id, orphan);
        place(item.at).calls.push(orphan);
      }
      return;
    }
    group = null;
    if (item.kind === "user" || item.kind === "assistant") {
      rows.push({ t: "message", who: item.kind === "user" ? "you" : "builder", id, at: item.at, text: item.text });
    } else {
      rows.push({ t: item.kind, id, at: item.at, text: item.text });
    }
  });
  for (const row of rows) {
    if (row.t === "tools") {
      row.label = toolLabel(row.calls) || "Used a tool";
      row.failed = row.calls.filter((c) => c.result?.isError).length;
    }
  }
  return rows;
}

/** `10:04` in the viewer's own time, or an empty string when the record has no time. */
export function clockTime(iso: string | null): string {
  if (!iso) return "";
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return "";
  return when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** What to show in place of a thread, or null when there is one to show. */
export function chatNotice(response: ChatResponse | null, held: number): string | null {
  if (response === null) return "Reading the conversation…";
  if (!response.supported) {
    if (response.backend === "codex")
      return "The chat view reads the agent's own session record, and Codex's is not wired in yet. Use the Terminal tab; this view follows.";
    if (response.backend === "kopicode")
      return "The chat view reads the agent's own session record, and kopicode's is not wired in yet. Use the Terminal tab; this view follows.";
    return "No builder has been started for this project. Give it a goal on the Overview and its conversation appears here.";
  }
  if (response.waiting)
    return "The builder is starting. Its conversation appears once Claude Code has written to its record. A new folder asks you to trust it first, in the Terminal tab.";
  if (held === 0)
    return response.session === "running" ? "Nothing has been said yet." : "This session has no messages.";
  return null;
}

export interface Composer {
  /** Whether the box takes a message now. */
  enabled: boolean;
  /** One line under the box: what sending does now, or why it cannot. */
  hint: string;
}

/** What the message box does, from the last read. Hidden (null) for a builder whose input is not
 * wired in (only Claude Code's is), so the box never offers what the daemon would refuse. */
export function composerState(response: ChatResponse | null): Composer | null {
  if (response === null || !response.supported) return null;
  if (response.session !== "running") {
    return { enabled: false, hint: "The builder is not running. Give it a goal on the Overview to start it." };
  }
  switch (response.activity?.state) {
    case "dialog":
      return {
        enabled: false,
        hint: "The builder is waiting on a permission. Answer it above, or in the Terminal tab, first: a message typed now would be read as the answer.",
      };
    case "busy":
      return {
        enabled: true,
        hint: "The builder is working. Your message is queued and read at its next step.",
      };
    case "idle":
      return { enabled: true, hint: "The builder is waiting for you." };
    default:
      return {
        enabled: true,
        hint: "No activity seen from the builder yet, so it is not known whether it is ready. A dialog open in the Terminal would stop the message.",
      };
  }
}
