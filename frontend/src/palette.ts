import type { RoleName, Theme } from "./types";

export type RGB = [number, number, number];

export function hexToRgb(hex: string): RGB {
  const h = hex.replace("#", "");
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as RGB;
}
export function rgba(hex: string, a: number): string {
  const [r, g, b] = hexToRgb(hex);
  return `rgba(${r},${g},${b},${a})`;
}
export function mixHex(a: string, b: string, t: number): string {
  const A = hexToRgb(a), B = hexToRgb(b);
  return "#" + A.map((v, i) => Math.round(v + (B[i] - v) * t).toString(16).padStart(2, "0")).join("");
}

/** The one palette object. DOM reads it as CSS custom properties; canvases read it directly. */
export class PaletteStore {
  theme!: Theme;
  private subs = new Set<(t: Theme) => void>();

  role(r: RoleName): string { return this.theme.roles[r]; }
  get light(): boolean { return this.theme.mode === "light"; }
  get mono(): boolean { return this.theme.mono; }

  subscribe(fn: (t: Theme) => void): void { this.subs.add(fn); }

  apply(theme: Theme): void {
    this.theme = theme;
    const root = document.documentElement;
    for (const [k, v] of Object.entries(theme.roles)) {
      root.style.setProperty(`--ffb-${k}`, v);
      root.style.setProperty(`--ffb-${k}-rgb`, hexToRgb(v).join(","));
    }
    root.dataset.mode = theme.mode;
    root.dataset.mono = String(theme.mono);
    root.style.setProperty("--ffb-font", theme.font ? `"${theme.font}"` : "ui-monospace");
    for (const fn of this.subs) fn(theme);
  }
}

export const palette = new PaletteStore();
