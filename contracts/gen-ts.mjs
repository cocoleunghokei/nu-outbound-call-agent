// Generates frontend/src/contracts/generated.ts from the JSON Schemas in this directory.
//
//   node gen-ts.mjs <output.ts>
//
// Output: one TypeScript type per schema (json-schema-to-typescript), open enums widened with
// `| (string & {})`, a KnownCallEvent discriminated union over the event types, and a
// parseCallEvent() decoder built from the same schemas.
import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const require = createRequire(join(here, "..", "frontend", "package.json"));
const { compile } = require("json-schema-to-typescript");

const outputPath = process.argv[2];
if (!outputPath) {
  console.error("usage: node gen-ts.mjs <output.ts>");
  process.exit(2);
}

const readSchema = (file) => JSON.parse(readFileSync(file, "utf8"));

function schemaFiles(dir) {
  const files = [];
  for (const entry of readdirSync(dir).sort()) {
    if (entry === "examples") continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) files.push(...schemaFiles(full));
    else if (entry.endsWith(".v1.json")) files.push(full);
  }
  return files;
}

const files = schemaFiles(here);
const titleOf = (file) => readSchema(file).title;

/** Copies a schema for TS generation: external $refs become named types, open enums widen. */
function prepare(node, file) {
  if (Array.isArray(node)) return node.map((item) => prepare(item, file));
  if (node === null || typeof node !== "object") return node;
  if (typeof node.$ref === "string" && !node.$ref.startsWith("#")) {
    return { tsType: titleOf(resolve(dirname(file), node.$ref)) };
  }
  const copy = {};
  for (const [key, value] of Object.entries(node)) {
    if (key === "$id" || key === "$schema" || key === "if" || key === "then") continue;
    copy[key] = prepare(value, file);
  }
  if (copy.allOf) {
    copy.allOf = copy.allOf.filter((branch) => Object.keys(branch).length > 0);
    if (copy.allOf.length === 0) delete copy.allOf;
  }
  if (copy.type === "object" && copy.properties === undefined && copy.additionalProperties === undefined) {
    copy.tsType = "Record<string, unknown>";
  }
  if (copy["x-open-enum"] === true && Array.isArray(copy.enum)) {
    const members = copy.enum.map((value) => JSON.stringify(value));
    copy.tsType = [...members.filter((m) => m !== "null"), "(string & {})", ...members.filter((m) => m === "null")].join(" | ");
    delete copy.enum;
  }
  return copy;
}

const compileOptions = { bannerComment: "", additionalProperties: false, format: true };

/**
 * Titled sub-schemas (for example PingState) appear in several schema files, and each
 * compile() call declares them again. Keep the first declaration of each name, drop identical
 * repeats, and fail on a name declared two different ways.
 */
const declared = new Map();
const isComment = (line) => /^\s*(\/\*\*|\*)/.test(line) || line.trim() === "";

function dedupeDeclarations(source) {
  const chunks = [];
  let current = [];
  for (const line of source.split("\n")) {
    const startsDeclaration = line.startsWith("/**") || line.startsWith("export ");
    if (startsDeclaration && !(line.startsWith("export ") && current.length > 0 && current.every(isComment))) {
      chunks.push(current);
      current = [];
    }
    current.push(line);
  }
  chunks.push(current);
  const kept = [];
  for (const chunk of chunks) {
    if (chunk.length === 0) continue;
    const text = chunk.join("\n");
    const name = /^export (?:type|interface) (\w+)/m.exec(text)?.[1];
    if (name === undefined) {
      kept.push(text);
      continue;
    }
    const previous = declared.get(name);
    if (previous === undefined) {
      declared.set(name, text.trim());
      kept.push(text);
    } else if (previous !== text.trim()) {
      throw new Error(`type ${name} is declared two different ways; give one a different title`);
    }
  }
  return kept.join("\n");
}

async function compileOnce(schema, name) {
  return dedupeDeclarations(await compile(schema, name, compileOptions));
}

const envelopeFile = join(here, "call-event.v1.json");
const envelope = readSchema(envelopeFile);
const eventTypes = envelope.allOf.map((branch) => branch.if.properties.type.const);
const payloadFile = (type) => join(here, "payloads", `${type}.v1.json`);

const parts = [];
for (const file of files) {
  const source = readSchema(file);
  const schema = prepare(source, file);
  if (typeof source.$ref === "string") {
    // A schema that only points at another one is an alias.
    parts.push(`export type ${source.title} = ${schema.tsType};\n`);
    continue;
  }
  parts.push(await compileOnce(schema, schema.title));
}

// KnownCallEvent: the envelope without `type` and `payload`, intersected with one member per type.
const baseSchema = prepare(envelope, envelopeFile);
baseSchema.title = "CallEventBase";
delete baseSchema.additionalProperties;
delete baseSchema.properties.type;
delete baseSchema.properties.payload;
baseSchema.required = baseSchema.required.filter((key) => key !== "type" && key !== "payload");
parts.push(await compileOnce(baseSchema, "CallEventBase"));

const members = eventTypes.map(
  (type) => `  | (CallEventBase & { type: ${JSON.stringify(type)}; payload: ${titleOf(payloadFile(type))} })`,
);
parts.push(
  `/** Every event type this client knows. Narrow on \`type\` to read the payload. */\n` +
    `export type KnownCallEvent =\n${members.join("\n")};\n`,
);
parts.push(
  `/** The known event types (the if/then branches of call-event.v1.json). */\n` +
    `export type CallEventType = KnownCallEvent["type"];\n`,
);

// Runtime schemas for parseCallEvent: $refs inlined, only the keywords the decoder understands.
const RUNTIME_KEYS = ["type", "required", "properties", "items", "const", "enum", "x-open-enum"];

function inline(node, file) {
  if (Array.isArray(node)) return node.map((item) => inline(item, file));
  if (node === null || typeof node !== "object") return node;
  if (typeof node.$ref === "string" && !node.$ref.startsWith("#")) {
    const target = resolve(dirname(file), node.$ref);
    return inline(readSchema(target), target);
  }
  const copy = {};
  for (const [key, value] of Object.entries(node)) copy[key] = inline(value, file);
  return copy;
}

function slim(node) {
  if (Array.isArray(node)) return node.map(slim);
  if (node === null || typeof node !== "object") return node;
  const copy = {};
  for (const key of RUNTIME_KEYS) {
    if (!(key in node)) continue;
    const value = node[key];
    if (key === "properties") {
      copy.properties = Object.fromEntries(Object.entries(value).map(([name, child]) => [name, slim(child)]));
    } else if (key === "items") {
      copy.items = slim(value);
    } else {
      copy[key] = value;
    }
  }
  return copy;
}

const envelopeRuntime = slim(inline({ ...envelope, allOf: undefined }, envelopeFile));
const payloadRuntime = Object.fromEntries(
  eventTypes.map((type) => [type, slim(inline(readSchema(payloadFile(type)), payloadFile(type)))]),
);

parts.push(`interface RuntimeSchema {
  type?: string | string[];
  required?: string[];
  properties?: Record<string, RuntimeSchema>;
  items?: RuntimeSchema;
  const?: unknown;
  enum?: unknown[];
  "x-open-enum"?: boolean;
}

const KNOWN_EVENT_TYPES: readonly string[] = ${JSON.stringify(eventTypes)};

const ENVELOPE_SCHEMA: RuntimeSchema = ${JSON.stringify(envelopeRuntime)};

const PAYLOAD_SCHEMAS: Record<string, RuntimeSchema> = ${JSON.stringify(payloadRuntime)};

function hasType(value: unknown, type: string): boolean {
  switch (type) {
    case "string":
      return typeof value === "string";
    case "number":
      return typeof value === "number" && Number.isFinite(value);
    case "integer":
      return typeof value === "number" && Number.isInteger(value);
    case "boolean":
      return typeof value === "boolean";
    case "null":
      return value === null;
    case "array":
      return Array.isArray(value);
    case "object":
      return typeof value === "object" && value !== null && !Array.isArray(value);
    default:
      return false;
  }
}

/** Checks required, type, const, closed enums, nested objects and arrays. Returns the first error. */
function checkValue(schema: RuntimeSchema, value: unknown, path: string): string | null {
  if (schema.type !== undefined) {
    const types = Array.isArray(schema.type) ? schema.type : [schema.type];
    if (!types.some((type) => hasType(value, type))) {
      return path + ": expected " + types.join(" or ");
    }
  }
  if ("const" in schema && value !== schema.const) {
    return path + ": expected constant " + JSON.stringify(schema.const);
  }
  if (schema.enum !== undefined && schema["x-open-enum"] !== true && !schema.enum.includes(value)) {
    return path + ": value is not one of the allowed values";
  }
  if (hasType(value, "object")) {
    const record = value as Record<string, unknown>;
    for (const key of schema.required ?? []) {
      if (!(key in record)) return path + "." + key + ": required";
    }
    for (const [key, child] of Object.entries(schema.properties ?? {})) {
      if (!(key in record)) continue;
      const error = checkValue(child, record[key], path + "." + key);
      if (error !== null) return error;
    }
  }
  if (Array.isArray(value) && schema.items !== undefined) {
    for (let index = 0; index < value.length; index += 1) {
      const error = checkValue(schema.items, value[index], path + "[" + index + "]");
      if (error !== null) return error;
    }
  }
  return null;
}

export type ParseCallEventResult =
  | { kind: "known"; event: KnownCallEvent }
  | { kind: "unknown"; raw: unknown }
  | { kind: "invalid"; raw: unknown; error: string };

/**
 * Decodes an untrusted event. An unknown \`type\` gives "unknown" (show "Unknown"); a malformed
 * envelope or payload gives "invalid" (drop it and warn). Extra fields are allowed, and values of
 * open enums are not checked against their set.
 */
export function parseCallEvent(raw: unknown): ParseCallEventResult {
  const envelopeError = checkValue(ENVELOPE_SCHEMA, raw, "event");
  if (envelopeError !== null) return { kind: "invalid", raw, error: envelopeError };
  const event = raw as { type: string; payload: unknown };
  if (!KNOWN_EVENT_TYPES.includes(event.type)) return { kind: "unknown", raw };
  const payloadError = checkValue(PAYLOAD_SCHEMAS[event.type], event.payload, "payload");
  if (payloadError !== null) return { kind: "invalid", raw, error: payloadError };
  return { kind: "known", event: raw as KnownCallEvent };
}
`);

const header =
  `// GENERATED by contracts/gen-ts.mjs from ${relative(resolve(here, ".."), here)}/*.v1.json. Do not edit.\n` +
  "// Regenerate with: make -C contracts gen\n\n";
const body = parts.join("\n").replace(/[ \t]+$/gm, "").replace(/\n{3,}/g, "\n\n").trimEnd();
writeFileSync(outputPath, `${header}${body}\n`);
