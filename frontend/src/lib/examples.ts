import type { Constraint } from "../api/types";

export interface Example {
  name: string;
  constraint: Constraint;
  prompt: string;
}

const regex = (text: string): Constraint => ({ kind: "regex", text, names: {} });
const expr = (text: string): Constraint => ({ kind: "expression", text, names: {} });

export const EXAMPLES: Example[] = [
  { name: "ISO date", constraint: regex("[0-9]{4}-[0-9]{2}-[0-9]{2}"), prompt: "Date: " },
  { name: "US phone number", constraint: regex("\\([0-9]{3}\\) [0-9]{3}-[0-9]{4}"), prompt: "Phone: " },
  { name: "Employee ID", constraint: regex("EMP-[0-9]{3,5}"), prompt: "Employee ID: " },
  { name: "Boolean-ish word", constraint: regex("(true|false|null)"), prompt: "Value: " },
  {
    name: "Email, no spam, 8–30 chars",
    constraint: expr("email & !contains(/spam/) & length(8, 30)"),
    prompt: "Contact: ",
  },
  { name: "Time that is not midnight", constraint: expr("time24 - /00:00/"), prompt: "Meeting at " },
];

export function asExpression(c: Constraint): string {
  return c.kind === "expression" ? c.text : `/${c.text.replace(/\//g, "\\/")}/`;
}

export function parseNames(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const at = line.indexOf("=");
    if (at > 0) out[line.slice(0, at).trim()] = line.slice(at + 1).trim();
  }
  return out;
}

export function formatNames(names: Record<string, string>): string {
  return Object.entries(names)
    .map(([k, v]) => `${k} = ${v}`)
    .join("\n");
}

export const DEFAULT_JSON_PROMPT = '{"name":"alice","age":30,"tags":["a","b"]}\n{"name":"bob","age":25,"tags":["c"]}\n';

export const DEFAULT_SCHEMA = JSON.stringify(
  {
    type: "object",
    properties: {
      name: { type: "string" },
      age: { type: "integer" },
      role: { enum: ["admin", "editor", "viewer"] },
      tags: { type: "array", items: { type: "string" } },
      active: { type: "boolean" },
    },
  },
  null,
  2,
);
