export type Theme = "light" | "dark" | "system";

export function getTheme(): Theme {
  try {
    return (localStorage.getItem("trio-theme") as Theme) || "system";
  } catch {
    return "system";
  }
}

export function applyTheme(theme: Theme) {
  try {
    localStorage.setItem("trio-theme", theme);
  } catch {
    /* storage unavailable */
  }
  const dark = theme === "dark" || (theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}
