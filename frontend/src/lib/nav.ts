// The shell's destinations. A later slice adds an entry here (Roles, Fleet) rather than
// editing the shell, so a destination only appears once its screen exists.

export interface NavItem {
  id: string;
  label: string;
  icon: string;
}

export const NAV_ITEMS: NavItem[] = [
  { id: "projects", label: "Projects", icon: "folder" },
  { id: "roles", label: "Roles", icon: "users" },
  { id: "sprites", label: "Sprites", icon: "sprite" },
];
