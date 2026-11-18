#!/usr/bin/env node
// Contract check for the ESLint transport rule: a fetch( or EventSource
// outside src/api/client.ts / src/api/sse.ts must be an eslint error, and the
// sanctioned modules must lint clean.
import { ESLint } from "eslint";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ui = join(dirname(fileURLToPath(import.meta.url)), "..");
const eslint = new ESLint({ cwd: ui });

async function errors(code, filePath) {
  const [res] = await eslint.lintText(code, { filePath: join(ui, filePath) });
  return res.messages.filter((m) => m.severity === 2);
}

const bad = [
  ["src/screens/Foo.tsx", "const r = await fetch('/v1/solves');"],
  ["src/screens/Foo.tsx", "const r = await window.fetch('/v1/solves');"],
  ["src/screens/Foo.tsx", "const s = new EventSource('/v1/solves/x/events');"],
  ["src/api/sse.ts", "const r = await fetch('/v1/solves');"],
];
for (const [file, code] of bad) {
  const errs = await errors(code, file);
  if (!errs.length) {
    console.error(`FAIL: no eslint error for ${code.trim()} in ${file}`);
    process.exit(1);
  }
}

const good = [
  ["src/api/client.ts", "const r = await fetch('/v1/solves');"],
  ["src/api/sse.ts", "const s = new EventSource('/v1/solves/x/events');"],
];
for (const [file, code] of good) {
  const errs = await errors(code, file);
  if (errs.length) {
    console.error(`FAIL: eslint error in sanctioned ${file}: ${errs[0].message}`);
    process.exit(1);
  }
}
console.log("check-no-fetch: fetch/EventSource banned outside client.ts and sse.ts");
