// The shell's destinations. A later slice adds an entry here (Roles, Needs you) rather than
// editing the shell, so a destination only appears once its screen exists.

export interface NavItem {
  id: string;
  label: string;
  icon: string;
  /** How many things wait on the person; shown as a badge, nothing at zero. */
  badge?: number;
}

export const NAV_ITEMS: NavItem[] = [
  { id: "needs-you", label: "Needs you", icon: "bell" },
  { id: "projects", label: "Projects", icon: "folder" },
  { id: "roles", label: "Roles", icon: "users" },
  { id: "secrets", label: "Secrets", icon: "lock" },
  { id: "sprites", label: "Sprites", icon: "sprite" },
];
