// Runtime test for the generated parseCallEvent. Run from contracts/ via `make test`.
// Compiles frontend/src/contracts/generated.ts into a temp dir with tsc, then imports it.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const tsc = join(here, "..", "frontend", "node_modules", ".bin", "tsc");
const source = join(here, "..", "frontend", "src", "contracts", "generated.ts");

const out = mkdtempSync(join(tmpdir(), "contracts-parse-"));
execFileSync(
  tsc,
  [source, "--outDir", out, "--module", "esnext", "--target", "es2020", "--skipLibCheck", "--strict"],
  { stdio: "inherit" },
);
writeFileSync(join(out, "package.json"), '{"type":"module"}');
const { parseCallEvent } = await import(pathToFileURL(join(out, "generated.js")).href);

const readJson = (path) => JSON.parse(readFileSync(path, "utf8"));
const eventsDir = join(here, "examples", "events");
const validFiles = readdirSync(eventsDir).filter((f) => f.endsWith(".valid.json"));
assert.equal(validFiles.length, 8, "one valid example per event type");

const base = readJson(join(here, "examples", "call-event.v1.valid.json"));
const cases = [];
const check = (name, fn) => cases.push([name, fn]);

for (const file of validFiles) {
  check(`valid example ${file} is known`, () => {
    const result = parseCallEvent(readJson(join(eventsDir, file)));
    assert.equal(result.kind, "known", JSON.stringify(result));
  });
}
check("call-event.v1.valid.json is known", () => {
  assert.equal(parseCallEvent(base).kind, "known");
});
check("null payload is invalid", () => {
  assert.equal(parseCallEvent({ ...base, payload: null }).kind, "invalid");
});
check("payload missing text is invalid", () => {
  const payload = { role: "agent", source_start_ts: null };
  assert.equal(parseCallEvent({ ...base, payload }).kind, "invalid");
});
check("closed enum role robot is invalid", () => {
  const payload = { ...base.payload, role: "robot" };
  assert.equal(parseCallEvent({ ...base, payload }).kind, "invalid");
});
check("non-object input is invalid", () => {
  assert.equal(parseCallEvent("nope").kind, "invalid");
  assert.equal(parseCallEvent(null).kind, "invalid");
});
check("missing envelope field is invalid", () => {
  const { seq: _seq, ...rest } = base;
  assert.equal(parseCallEvent(rest).kind, "invalid");
});
check("unknown type is unknown", () => {
  const raw = { ...base, type: "future.thing", payload: { a: 1 } };
  const result = parseCallEvent(raw);
  assert.equal(result.kind, "unknown");
  assert.deepEqual(result.raw, raw);
});
check("extra fields still parse as known", () => {
  const payload = { ...base.payload, extra: 1 };
  assert.equal(parseCallEvent({ ...base, payload, new_field: true }).kind, "known");
});
check("unknown status value (open enum) still parses as known", () => {
  const raw = { ...base, type: "status.changed", payload: { status: "teleporting" } };
  assert.equal(parseCallEvent(raw).kind, "known");
});
check("unknown outcome value (open enum, nullable) still parses as known", () => {
  const raw = { ...base, type: "call.ended", payload: { outcome: "new_outcome", ended_reason: "x" } };
  assert.equal(parseCallEvent(raw).kind, "known");
});

let failed = 0;
for (const [name, fn] of cases) {
  try {
    fn();
    console.log(`ok   ${name}`);
  } catch (error) {
    failed += 1;
    console.log(`FAIL ${name}\n${error.message}`);
  }
}
console.log(`${cases.length - failed}/${cases.length} passed`);
process.exit(failed === 0 ? 0 : 1);
