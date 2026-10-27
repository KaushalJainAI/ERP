/**
 * i18n completeness check.  Usage:
 *   npm run i18n:check                 report + exit 1 on any problem
 *   node scripts/check-i18n.mjs --dump <file>   write the flattened English bundle as JSON
 *
 * English is the union of src/i18n/en.ts and src/i18n/en.*.ts (every exported object,
 * merged the same way i18n.ts spreads them) plus the base keys in en.core.ts.
 * Each other language is src/i18n/locales/<code>.ts (default export, same nested shape).
 *
 * Reports per language: missing keys, extra keys, empty values, {{placeholder}} mismatches.
 * Values equal to English are listed as a count only (allowed: GST, GSTIN, HSN ... are
 * deliberately kept in English); keys listed in KEEP_ENGLISH below are never counted.
 */
import { build } from "esbuild";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const i18nDir = path.join(root, "src", "i18n");
const LANGS = ["hi", "bn", "ta", "te", "mr", "gu", "kn", "ml", "pa", "or", "as", "ur"];

async function importTs(file) {
  const r = await build({
    entryPoints: [file],
    bundle: true,
    write: false,
    format: "esm",
    platform: "neutral",
    logLevel: "silent",
  });
  const code = r.outputFiles[0].text;
  return import("data:text/javascript;base64," + Buffer.from(code).toString("base64"));
}

function flatten(obj, prefix = "", out = {}) {
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === "object") flatten(v, key, out);
    else out[key] = v;
  }
  return out;
}

export async function loadEnglish() {
  const files = fs
    .readdirSync(i18nDir)
    .filter((f) => f === "en.ts" || (/^en\..+\.ts$/.test(f) && f !== "en.core.ts"))
    .sort();
  const merged = {};
  const core = path.join(i18nDir, "en.core.ts");
  const order = fs.existsSync(core) ? [path.basename(core), ...files] : files;
  for (const f of order) {
    const mod = await importTs(path.join(i18nDir, f));
    for (const v of Object.values(mod)) if (v && typeof v === "object") Object.assign(merged, v);
  }
  return flatten(merged);
}

async function loadLocale(code) {
  const file = path.join(i18nDir, "locales", `${code}.ts`);
  if (!fs.existsSync(file)) return null;
  const mod = await importTs(file);
  return flatten(mod.default ?? {});
}

const placeholders = (s) => [...String(s).matchAll(/\{\{\s*([\w.]+)\s*\}\}/g)].map((m) => m[1]).sort().join(",");

/** Keys whose value is intentionally identical to English everywhere. */
const KEEP_ENGLISH = new Set(["appName"]);

async function main() {
  const en = await loadEnglish();
  const dumpAt = process.argv.indexOf("--dump");
  if (dumpAt > -1) {
    fs.writeFileSync(process.argv[dumpAt + 1], JSON.stringify(en, null, 1));
    console.log(`${Object.keys(en).length} English keys written`);
    return;
  }
  const enKeys = Object.keys(en);
  let failed = false;
  console.log(`English: ${enKeys.length} keys`);
  for (const code of LANGS) {
    const loc = await loadLocale(code);
    if (!loc) {
      console.log(`${code}: MISSING FILE src/i18n/locales/${code}.ts`);
      failed = true;
      continue;
    }
    const missing = enKeys.filter((k) => !(k in loc));
    const extra = Object.keys(loc).filter((k) => !(k in en));
    const empty = Object.keys(loc).filter((k) => k in en && String(loc[k]).trim() === "");
    const badPh = Object.keys(loc).filter((k) => k in en && placeholders(loc[k]) !== placeholders(en[k]));
    const same = enKeys.filter((k) => k in loc && loc[k] === en[k] && !KEEP_ENGLISH.has(k));
    const ok = !missing.length && !extra.length && !empty.length && !badPh.length;
    if (!ok) failed = true;
    const pct = (((enKeys.length - missing.length) / enKeys.length) * 100).toFixed(1);
    console.log(
      `${code}: ${ok ? "OK" : "FAIL"}  ${enKeys.length - missing.length}/${enKeys.length} (${pct}%)  ` +
        `missing ${missing.length}, extra ${extra.length}, empty ${empty.length}, placeholder mismatches ${badPh.length}, same-as-English ${same.length}`
    );
    const show = (label, list) => list.length && console.log(`    ${label}: ${list.slice(0, 40).join(", ")}${list.length > 40 ? ` ... (+${list.length - 40})` : ""}`);
    show("missing", missing);
    show("extra", extra);
    show("empty", empty);
    show("placeholder mismatch", badPh);
    if (process.argv.includes("--verbose")) show("same-as-English", same);
  }
  if (failed) {
    console.error("\ni18n check FAILED");
    process.exit(1);
  }
  console.log("\ni18n check passed");
}

main();
