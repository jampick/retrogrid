// Data-sprite loading + palette mapping, shared by ghosts, cards and rails.
//   R = tone index (0..3 -> 0,85,170,255)   G = region (255 jersey, 0 skin/hair)
//   B = silhouette edge (255)               A = mask

import { hexToRgb, mixHex, palette } from "./palette";

export const SP = 96;
const cache = new Map<string, Promise<ImageData | null>>();

export function loadSprite(url: string): Promise<ImageData | null> {
  let p = cache.get(url);
  if (!p) {
    p = new Promise((resolve) => {
      const img = new Image();
      img.onload = () => {
        const c = document.createElement("canvas"); c.width = c.height = SP;
        const x = c.getContext("2d")!; x.drawImage(img, 0, 0, SP, SP);
        resolve(x.getImageData(0, 0, SP, SP));
      };
      img.onerror = () => resolve(null);
      img.src = url;
    });
    cache.set(url, p);
  }
  return p;
}

let bust: ImageData | null = null;
/** "No image" must not look like "bad image": an edge-only wireframe bust. */
export function proceduralBust(): ImageData {
  if (bust) return bust;
  const c = document.createElement("canvas"); c.width = c.height = SP;
  const x = c.getContext("2d")!;
  x.fillStyle = "#000";
  x.beginPath(); x.ellipse(48, 36, 17, 21, 0, 0, Math.PI * 2); x.fill();
  x.beginPath(); x.moveTo(8, 96); x.quadraticCurveTo(10, 64, 38, 58); x.lineTo(58, 58); x.quadraticCurveTo(86, 64, 88, 96); x.fill();
  const d = x.getImageData(0, 0, SP, SP).data;
  const solid = (i: number) => d[i * 4 + 3] > 128;
  const out = new ImageData(SP, SP);
  for (let y = 1; y < SP - 1; y++) for (let X = 1; X < SP - 1; X++) {
    const i = y * SP + X;
    if (!solid(i)) continue;
    const edge = !solid(i - 1) || !solid(i + 1) || !solid(i - SP) || !solid(i + SP);
    if (edge || X % 8 === 0 || y % 8 === 0) {
      out.data[i * 4] = edge ? 255 : 85; out.data[i * 4 + 2] = edge ? 255 : 0; out.data[i * 4 + 3] = 255;
    }
  }
  return (bust = out);
}

/** Palette-map a data sprite for one side under the current theme. */
export function tint(src: ImageData, side: "you" | "them" | "hot"): HTMLCanvasElement {
  const base = palette.role(side === "hot" ? "dim" : side), hot = palette.role("hot"), bg = palette.role("bg");
  const floor = palette.light ? bg : "#000000";
  const ramp = (b: string) => [0.22, 0.45, 0.72, 1].map((t) => hexToRgb(mixHex(floor, b, t)));
  const jersey = ramp(base), skin = ramp(mixHex(base, hot, 0.55));
  const edge = hexToRgb(palette.light ? base : mixHex(base, hot, 0.5));
  const out = new ImageData(SP, SP);
  for (let i = 0; i < SP * SP; i++) {
    const a = src.data[i * 4 + 3];
    if (a < 40) continue;
    const tone = Math.min(3, Math.round(src.data[i * 4] / 85));
    const c = src.data[i * 4 + 2] > 128 ? edge : (src.data[i * 4 + 1] > 128 ? jersey : skin)[tone];
    out.data[i * 4] = c[0]; out.data[i * 4 + 1] = c[1]; out.data[i * 4 + 2] = c[2]; out.data[i * 4 + 3] = a;
  }
  const c = document.createElement("canvas"); c.width = c.height = SP;
  c.getContext("2d")!.putImageData(out, 0, 0);
  return c;
}
