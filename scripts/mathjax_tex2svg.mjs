#!/usr/bin/env node
/**
 * stdin JSON: { "tex": "y = ax + b", "display": true, "color": "#e53935" }
 * stdout: pure SVG markup (UTF-8) — no mjx-container wrapper
 */
import { mathjax } from "mathjax-full/js/mathjax.js";
import { TeX } from "mathjax-full/js/input/tex.js";
import { SVG } from "mathjax-full/js/output/svg.js";
import { liteAdaptor } from "mathjax-full/js/adaptors/liteAdaptor.js";
import { RegisterHTMLHandler } from "mathjax-full/js/handlers/html.js";
import { AllPackages } from "mathjax-full/js/input/tex/AllPackages.js";

function readStdin() {
  return new Promise((resolve, reject) => {
    const chunks = [];
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (c) => chunks.push(c));
    process.stdin.on("end", () => resolve(chunks.join("")));
    process.stdin.on("error", reject);
  });
}

function normalizeTex(raw) {
  let s = String(raw || "").trim();
  if (s.startsWith("$$") && s.endsWith("$$") && s.length >= 4) s = s.slice(2, -2).trim();
  else if (s.startsWith("$") && s.endsWith("$") && s.length >= 2 && (s.match(/\$/g) || []).length === 2) {
    s = s.slice(1, -1).trim();
  }
  s = s
    .replace(/\u00a0/g, " ")
    .replace(/\u202f/g, " ")
    .replace(/\u2007/g, " ")
    .replace(/\u00ad/g, "-")
    .replace(/\u2212/g, "-")
    .replace(/\u2013/g, "-")
    .replace(/\u2014/g, "-")
    .replace(/[ \t]+/g, " ")
    .trim();
  return s;
}

function extractSvg(html) {
  const s = String(html || "");
  const start = s.indexOf("<svg");
  const end = s.lastIndexOf("</svg>");
  if (start < 0 || end < 0 || end <= start) {
    throw new Error("mathjax did not produce svg");
  }
  return s.slice(start, end + "</svg>".length);
}

function paintSvg(svg, color) {
  let out = svg;
  // ensure root has xmlns
  if (!/\sxmlns=/.test(out.slice(0, 200))) {
    out = out.replace(/<svg\b/, '<svg xmlns="http://www.w3.org/2000/svg"');
  }
  // MathJax often uses currentColor / black strokes
  out = out.replace(/currentColor/g, color);
  out = out.replace(/stroke="black"/g, `stroke="${color}"`);
  out = out.replace(/fill="black"/g, `fill="${color}"`);
  out = out.replace(/stroke='#000(?:000)?'/g, `stroke='${color}'`);
  out = out.replace(/fill='#000(?:000)?'/g, `fill='${color}'`);
  // if groups have no fill, set default on root
  if (!/fill="/.test(out.slice(0, 300))) {
    out = out.replace(/<svg\b/, `<svg fill="${color}" stroke="${color}"`);
  }
  return out;
}

async function main() {
  const raw = (await readStdin()).trim();
  if (!raw) {
    console.error("empty stdin");
    process.exit(2);
  }
  let payload;
  try {
    payload = JSON.parse(raw);
  } catch (e) {
    payload = { tex: raw, display: true };
  }
  const tex = normalizeTex(payload.tex || payload.latex || "");
  if (!tex) {
    console.error("empty tex");
    process.exit(2);
  }
  const display = payload.display !== false;
  const color = String(payload.color || "#e53935").trim() || "#e53935";

  const adaptor = liteAdaptor();
  RegisterHTMLHandler(adaptor);
  const html = mathjax.document("", {
    InputJax: new TeX({ packages: AllPackages }),
    OutputJax: new SVG({ fontCache: "none" }),
  });
  const node = html.convert(tex, { display });
  const wrapped = adaptor.outerHTML(node);
  const svg = paintSvg(extractSvg(wrapped), color);
  process.stdout.write(svg);
}

main().catch((err) => {
  console.error(String(err && err.stack ? err.stack : err));
  process.exit(1);
});
