#!/usr/bin/env node
/*
 * build_deck.js - opinionated pptxgenjs deck renderer.
 *
 * You write a JSON spec (content only); this lays it out as a DESIGNED deck:
 * dark cover/closing, light content, shadowed cards, real timeline rails,
 * stat callouts, accent badges. It is impossible to emit a bare bullet wall.
 *
 *   node build_deck.js <spec.json> <out.pptx>
 *
 * Spec shape:
 * {
 *   "title": "...", "subtitle": "...", "theme": "forest",
 *   "slides": [
 *     { "type":"cover",   "title":"...", "subtitle":"..." },
 *     { "type":"timeline","heading":"1972-1992",
 *       "events":[ {"date":"1972","title":"Stockholm","text":"..."} ] },
 *     { "type":"cards",   "heading":"...","subhead":"...",
 *       "cards":[ {"badge":"UNEP","title":"...","text":"..."} ] },
 *     { "type":"stats",   "heading":"...",
 *       "stats":[ {"value":"196","label":"parties to Paris"} ] },
 *     { "type":"split",   "heading":"...","lead":"big idea",
 *       "points":["...","..."] },
 *     { "type":"closing", "title":"...","subtitle":"..." }
 *   ]
 * }
 * themes: forest | tech | corporate | gaming | academic
 */
// Resolve pptxgenjs whether it's installed locally OR globally (npm i -g).
let pptxgen;
try {
  pptxgen = require("pptxgenjs");
} catch (e) {
  try {
    const root = require("child_process").execSync("npm root -g", { encoding: "utf8" }).trim();
    pptxgen = require(require("path").join(root, "pptxgenjs"));
  } catch (e2) {
    console.error("pptxgenjs not found. Install it once:  npm install -g pptxgenjs");
    process.exit(3);
  }
}

const THEMES = {
  forest:    { dark: "0E1A14", panel: "13241B", light: "F4F8F4", ink: "12211A", dim: "5C6B62", accent: "2FB573", accent2: "7FD9A8", line: "D9E5DD" },
  tech:      { dark: "0C0F1F", panel: "141934", light: "F6F7FB", ink: "14182E", dim: "5E6480", accent: "6C5CE7", accent2: "A78BFA", line: "E0E2EE" },
  corporate: { dark: "10182B", panel: "182338", light: "FBFAF6", ink: "16203A", dim: "6B6F7A", accent: "C9A227", accent2: "E3C766", line: "E7E4DA" },
  gaming:    { dark: "08080C", panel: "131320", light: "F2F2F5", ink: "111118", dim: "6A6A78", accent: "FF4D6D", accent2: "FF9FB2", line: "27272F" },
  academic:  { dark: "1A1A1A", panel: "242424", light: "FFFFFF", ink: "1A1A1A", dim: "707070", accent: "2563EB", accent2: "7AA2F7", line: "E5E5E5" },
};
const FONT = "Segoe UI";

function rgb(c, p, s) { return [c, p, s]; }

function main() {
  const [, , specPath, outPath] = process.argv;
  if (!specPath || !outPath) { console.error("usage: node build_deck.js <spec.json> <out.pptx>"); process.exit(2); }
  const spec = JSON.parse(require("fs").readFileSync(specPath, "utf8"));
  const T = THEMES[spec.theme] || THEMES.tech;

  const pptx = new pptxgen();
  pptx.defineLayout({ name: "W16x9", width: 13.333, height: 7.5 });
  pptx.layout = "W16x9";
  pptx.theme = { headFontFace: FONT, bodyFontFace: FONT };

  const W = 13.333, H = 7.5, M = 0.85;
  const shadow = { type: "outer", color: "000000", blur: 9, offset: 4, angle: 90, opacity: 0.34 };

  // accent badge: filled rounded square with short text (date / acronym / number)
  function badge(slide, x, y, text, { size = 1.0, fs = 16, fill = T.accent, col = "FFFFFF" } = {}) {
    slide.addShape(pptx.ShapeType.roundRect, { x, y, w: size, h: size, fill: { color: fill }, line: { type: "none" }, rectRadius: 0.12, shadow });
    slide.addText(String(text), { x, y, w: size, h: size, align: "center", valign: "middle", fontFace: FONT, fontSize: fs, bold: true, color: col });
  }
  function card(slide, x, y, w, h) {
    // shadow gives the depth; a border + shadow together trips some viewers, and
    // shadow-only reads cleaner anyway.
    slide.addShape(pptx.ShapeType.roundRect, { x, y, w, h, fill: { color: "FFFFFF" }, line: { type: "none" }, rectRadius: 0.08, shadow });
  }
  function heading(slide, text, sub) {
    slide.addShape(pptx.ShapeType.rect, { x: M, y: 0.62, w: 0.16, h: 0.62, fill: { color: T.accent }, line: { type: "none" } });
    slide.addText(text, { x: M + 0.32, y: 0.5, w: W - 2 * M - 0.3, h: 0.85, fontFace: FONT, fontSize: 30, bold: true, color: T.ink });
    if (sub) slide.addText(sub, { x: M + 0.34, y: 1.32, w: W - 2 * M, h: 0.5, fontFace: FONT, fontSize: 15, color: T.dim });
  }

  for (const s of spec.slides) {
    const slide = pptx.addSlide();

    if (s.type === "cover") {
      slide.background = { color: T.dark };
      slide.addShape(pptx.ShapeType.rect, { x: 0, y: H - 0.14, w: W, h: 0.14, fill: { color: T.accent }, line: { type: "none" } });
      slide.addText((s.eyebrow || "").toUpperCase(), { x: M, y: 2.05, w: W - 2 * M, h: 0.4, fontFace: FONT, fontSize: 15, bold: true, color: T.accent2, charSpacing: 3 });
      slide.addText(s.title || spec.title || "", { x: M, y: 2.5, w: W - 2 * M, h: 2.0, fontFace: FONT, fontSize: 54, bold: true, color: "FFFFFF", lineSpacingMultiple: 0.98 });
      slide.addText(s.subtitle || spec.subtitle || "", { x: M, y: 4.55, w: W - 2 * M, h: 0.9, fontFace: FONT, fontSize: 21, color: T.line });
      continue;
    }

    if (s.type === "closing") {
      slide.background = { color: T.dark };
      slide.addText(s.title || "", { x: M, y: 2.7, w: W - 2 * M, h: 1.6, align: "center", fontFace: FONT, fontSize: 46, bold: true, color: "FFFFFF" });
      slide.addText(s.subtitle || "", { x: M, y: 4.3, w: W - 2 * M, h: 0.9, align: "center", fontFace: FONT, fontSize: 20, color: T.accent2 });
      continue;
    }

    // all content slides: light bg
    slide.background = { color: T.light };

    if (s.type === "timeline") {
      heading(slide, s.heading || "Timeline", s.subhead);
      const ev = (s.events || []).slice(0, 5);
      const n = ev.length || 1;
      const railY = 4.1, x0 = M + 0.2, x1 = W - M - 0.2;
      slide.addShape(pptx.ShapeType.line, { x: x0, y: railY, w: x1 - x0, h: 0, line: { color: T.accent, width: 3 } });
      const step = (x1 - x0) / n;
      ev.forEach((e, i) => {
        const cx = x0 + step * i + step / 2;
        const up = i % 2 === 0;
        const cw = Math.min(2.35, step - 0.25), ch = 1.65;
        const cardX = cx - cw / 2;
        const cardY = up ? railY - 0.55 - ch : railY + 0.55;
        // connector + node
        slide.addShape(pptx.ShapeType.line, { x: cx, y: up ? cardY + ch : railY, w: 0, h: 0.55, line: { color: T.line, width: 1.5 } });
        slide.addShape(pptx.ShapeType.ellipse, { x: cx - 0.11, y: railY - 0.11, w: 0.22, h: 0.22, fill: { color: T.accent }, line: { color: "FFFFFF", width: 2 } });
        card(slide, cardX, cardY, cw, ch);
        slide.addText(String(e.date || ""), { x: cardX, y: cardY + 0.12, w: cw, h: 0.4, align: "center", fontFace: FONT, fontSize: 17, bold: true, color: T.accent });
        slide.addText(e.title || "", { x: cardX + 0.12, y: cardY + 0.52, w: cw - 0.24, h: 0.4, align: "center", fontFace: FONT, fontSize: 13, bold: true, color: T.ink });
        slide.addText(e.text || "", { x: cardX + 0.14, y: cardY + 0.9, w: cw - 0.28, h: ch - 0.95, align: "center", fontFace: FONT, fontSize: 10.5, color: T.dim, lineSpacingMultiple: 0.95 });
      });
      continue;
    }

    if (s.type === "cards") {
      heading(slide, s.heading || "", s.subhead);
      const cards = (s.cards || []).slice(0, 6);
      const n = cards.length;
      const cols = n <= 2 ? n : n <= 4 ? 2 : 3;
      const rows = Math.ceil(n / cols);
      const gap = 0.32, top = 2.0, areaH = H - top - 0.6;
      const cw = (W - 2 * M - (cols - 1) * gap) / cols;
      const ch = (areaH - (rows - 1) * gap) / rows;
      cards.forEach((c, i) => {
        const r = Math.floor(i / cols), col = i % cols;
        const x = M + col * (cw + gap), y = top + r * (ch + gap);
        card(slide, x, y, cw, ch);
        badge(slide, x + 0.28, y + 0.28, (c.badge || (i + 1)).toString().slice(0, 5), { size: 0.78, fs: c.badge && c.badge.length > 2 ? 13 : 18 });
        slide.addText(c.title || "", { x: x + 1.2, y: y + 0.26, w: cw - 1.45, h: 0.85, fontFace: FONT, fontSize: 17, bold: true, color: T.ink, valign: "middle" });
        slide.addText(c.text || "", { x: x + 0.3, y: y + 1.2, w: cw - 0.6, h: ch - 1.4, fontFace: FONT, fontSize: 12.5, color: T.dim, lineSpacingMultiple: 1.0 });
      });
      continue;
    }

    if (s.type === "stats") {
      heading(slide, s.heading || "", s.subhead);
      const st = (s.stats || []).slice(0, 4);
      const n = st.length || 1;
      const gap = 0.4, cw = (W - 2 * M - (n - 1) * gap) / n, top = 2.6, ch = 2.7;
      st.forEach((d, i) => {
        const x = M + i * (cw + gap);
        card(slide, x, top, cw, ch);
        slide.addText(String(d.value || ""), { x, y: top + 0.5, w: cw, h: 1.2, align: "center", fontFace: FONT, fontSize: 52, bold: true, color: T.accent });
        slide.addText(d.label || "", { x: x + 0.25, y: top + 1.75, w: cw - 0.5, h: 0.8, align: "center", fontFace: FONT, fontSize: 14, color: T.dim });
      });
      continue;
    }

    if (s.type === "split") {
      heading(slide, s.heading || "", s.subhead);
      slide.addShape(pptx.ShapeType.roundRect, { x: M, y: 2.1, w: 4.7, h: H - 2.1 - 0.7, fill: { color: T.panel }, line: { type: "none" }, rectRadius: 0.06, shadow });
      slide.addText(s.lead || "", { x: M + 0.4, y: 2.5, w: 4.0, h: H - 2.1 - 1.4, fontFace: FONT, fontSize: 24, bold: true, color: "FFFFFF", valign: "middle", lineSpacingMultiple: 1.05 });
      const pts = (s.points || []).slice(0, 5);
      const top = 2.2, areaH = H - top - 0.7, ph = areaH / Math.max(pts.length, 1);
      pts.forEach((p, i) => {
        const y = top + i * ph;
        slide.addShape(pptx.ShapeType.ellipse, { x: 6.1, y: y + ph / 2 - 0.13, w: 0.26, h: 0.26, fill: { color: T.accent }, line: { type: "none" } });
        slide.addText(p, { x: 6.6, y, w: W - M - 6.6, h: ph, fontFace: FONT, fontSize: 16, color: T.ink, valign: "middle", lineSpacingMultiple: 1.0 });
      });
      continue;
    }

    // unknown type -> a clean title card so we never crash to blank
    heading(slide, s.heading || s.title || "Slide");
    slide.addText(s.text || "", { x: M, y: 2.2, w: W - 2 * M, h: 3, fontFace: FONT, fontSize: 18, color: T.dim });
  }

  // Only builds + prints the path. Opening is the agent's job (a separate,
  // visible run_command) so the user sees and can gate it.
  pptx.writeFile({ fileName: outPath }).then(() => console.log(outPath));
}
main();
