// 3x5 bitmap face. One font for field tags (1x) and display numerals (Nx):
// chunky upscaled blocks read as LED matrix, which is the point.

const G: Record<string, string> = {
  A: "010101111101101", B: "110101110101110", C: "011100100100011", D: "110101101101110",
  E: "111100110100111", F: "111100110100100", G: "011100101101011", H: "101101111101101",
  I: "111010010010111", J: "001001001101010", K: "101101110101101", L: "100100100100111",
  M: "101111111101101", N: "110101101101101", O: "010101101101010", P: "110101110100100",
  Q: "010101101111011", R: "110101110101101", S: "011100010001110", T: "111010010010010",
  U: "101101101101111", V: "101101101101010", W: "101101111111101", X: "101101010101101",
  Y: "101101010010010", Z: "111001010100111",
  "0": "111101101101111", "1": "010110010010111", "2": "110001010100111", "3": "110001010001110",
  "4": "101101111001001", "5": "111100110001110", "6": "011100111101111", "7": "111001010010010",
  "8": "111101111101111", "9": "111101111001110",
  ".": "000000000000010", "+": "000010111010000", "-": "000000111000000", "'": "010010000000000",
  "/": "001001010100100", ":": "000010000010000", "@": "111101111100111", "&": "010101010101011",
  "_": "000000000000111", "#": "101111101111101", "%": "101001010100101", "!": "010010010000010",
  "?": "110001010000010", "<": "001010100010001", ">": "100010001010100", "*": "000101010101000",
  "{": "000010111111000", "}": "000111111010000",     // { = up arrow, } = down arrow
};

export const GLYPH_W = 3, GLYPH_H = 5, ADVANCE = 4;

export function textWidth(s: string): number {
  return s.length ? s.length * ADVANCE - 1 : 0;
}

/** Draw into a 2D context at integer pixel coords; `px` is the block size. */
export function drawText(ctx: CanvasRenderingContext2D, s: string, x: number, y: number, color: string, px = 1): void {
  ctx.fillStyle = color;
  let cx = Math.round(x);
  const cy = Math.round(y);
  for (const ch of s.toUpperCase()) {
    const g = G[ch];
    if (g) {
      for (let i = 0; i < 15; i++) {
        if (g[i] === "1") ctx.fillRect(cx + (i % 3) * px, cy + ((i / 3) | 0) * px, px, px);
      }
    }
    cx += ADVANCE * px;
  }
}

/** A self-sizing <canvas> holding pixel text, for DOM display type. */
export class PixelLabel {
  readonly el: HTMLCanvasElement;
  private last = "";
  constructor(private px: number, private role: string, cls = "") {
    this.el = document.createElement("canvas");
    this.el.className = `pxl ${cls}`;
  }
  set(text: string, role = this.role): void {
    const key = `${text}|${role}|${getComputedStyle(document.documentElement).getPropertyValue(`--ffb-${role}`)}`;
    if (key === this.last) return;
    this.last = key;
    this.role = role;
    const color = key.split("|")[2].trim() || "#fff";
    const w = Math.max(1, textWidth(text) * this.px), h = GLYPH_H * this.px;
    this.el.width = w; this.el.height = h;
    this.el.style.width = `${w}px`; this.el.style.height = `${h}px`;
    const ctx = this.el.getContext("2d")!;
    ctx.clearRect(0, 0, w, h);
    drawText(ctx, text, 0, 0, color, this.px);
    this.el.style.setProperty("--glow", color);
  }
  refresh(): void { const t = this.last.split("|")[0]; this.last = ""; this.set(t); }
}
