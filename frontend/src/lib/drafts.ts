// Unsaved edits that outlive a screen. A project's Permissions and Team tabs hold what a person
// has typed but not saved; leaving the project (the Back button, the rail) unmounts the page, and
// the edits used to go with it. They are kept here, in memory for this browser tab, keyed by
// project, and dropped when saved or discarded. A reload still loses them: the page warns first.

const store = new Map<string, unknown>();

const key = (projectId: string, part: string) => `${projectId}\u0000${part}`;

/** The unsaved edit kept for `part` of a project (`"permissions"`, `"limits"`, `"role:<name>"`). */
export function loadDraft<T>(projectId: string, part: string): T | undefined {
  return store.get(key(projectId, part)) as T | undefined;
}

export function saveDraft(projectId: string, part: string, value: unknown): void {
  store.set(key(projectId, part), value);
}

export function clearDraft(projectId: string, part: string): void {
  store.delete(key(projectId, part));
}

/** Whether a project has any unsaved edit kept (a draft equal to the saved value is cleared, so
 * what is here is a real difference). */
export function hasDrafts(projectId: string): boolean {
  const prefix = `${projectId}\u0000`;
  for (const name of store.keys()) if (name.startsWith(prefix)) return true;
  return false;
}

/** Forget every draft (tests). */
export function resetDrafts(): void {
  store.clear();
}
