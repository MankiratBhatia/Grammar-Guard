import { useState } from "react";
import type { Constraint } from "../api/types";
import { EXAMPLES, formatNames, parseNames } from "../lib/examples";
import { useBuiltins } from "../lib/hooks";
import { Field, Segmented, TextArea, TextInput, cx } from "./ui";

const FEATURED = ["digits", "email", "iso_date", "time24", "us_phone", "uuid", "hex_color", "username", "semver"];

/** Edits a Constraint: a plain regex, or an expression in the constraint algebra (with named sub-constraints). */
export function ConstraintInput({
  value,
  onChange,
  label = "Constraint",
  kinds = ["regex", "expression"],
  examples = true,
  onExample,
}: {
  value: Constraint;
  onChange: (c: Constraint) => void;
  label?: string;
  kinds?: ("regex" | "expression")[];
  examples?: boolean;
  onExample?: (prompt: string) => void;
}) {
  const builtins = useBuiltins();
  const [namesText, setNamesText] = useState(formatNames(value.names ?? {}));
  const [showNames, setShowNames] = useState(Object.keys(value.names ?? {}).length > 0);
  const isExpr = value.kind === "expression";

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs font-medium text-muted">{label}</span>
        <div className="flex items-center gap-2">
          {examples && (
            <select
              aria-label={`${label} examples`}
              className="rounded-lg border border-line bg-panel2 px-2 py-1 text-xs text-ink"
              value=""
              onChange={(e) => {
                const ex = EXAMPLES.find((x) => x.name === e.target.value);
                if (!ex) return;
                onChange(ex.constraint);
                setNamesText(formatNames(ex.constraint.names ?? {}));
                onExample?.(ex.prompt);
              }}
            >
              <option value="">Examples…</option>
              {EXAMPLES.filter((x) => kinds.includes(x.constraint.kind)).map((x) => (
                <option key={x.name}>{x.name}</option>
              ))}
            </select>
          )}
          {kinds.length > 1 && (
            <Segmented
              label={`${label} type`}
              value={value.kind ?? "regex"}
              onChange={(kind) => onChange({ ...value, kind })}
              options={[
                { value: "regex", label: "Regex" },
                { value: "expression", label: "Algebra" },
              ]}
            />
          )}
        </div>
      </div>
      {isExpr ? (
        <TextArea
          aria-label={label}
          mono
          rows={2}
          value={value.text}
          onChange={(e) => onChange({ ...value, text: e.target.value })}
          placeholder="email & !contains(/spam/) & length(8, 30)"
        />
      ) : (
        <TextInput
          aria-label={label}
          mono
          value={value.text}
          onChange={(e) => onChange({ ...value, text: e.target.value })}
          placeholder="[0-9]{4}-[0-9]{2}-[0-9]{2}"
        />
      )}
      {isExpr && (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[11px] text-muted">Insert:</span>
            {FEATURED.filter((n) => n in builtins).map((n) => (
              <button
                key={n}
                type="button"
                title={builtins[n]}
                className="rounded-md border border-line bg-panel2 px-1.5 py-0.5 font-mono text-[11px] text-ink hover:border-accent"
                onClick={() => onChange({ ...value, text: value.text.trim() ? `${value.text.trim()} & ${n}` : n })}
              >
                {n}
              </button>
            ))}
            <button type="button" className="text-[11px] text-accent underline" onClick={() => setShowNames((s) => !s)}>
              {showNames ? "hide" : "define"} custom names
            </button>
          </div>
          <p className="text-[11px] leading-relaxed text-muted">
            Operators: <code>&amp;</code> and, <code>|</code> or, <code>-</code> minus, <code>!</code> not. Functions:{" "}
            <code>length(n, m)</code>, <code>contains(x)</code>, <code>starts(x)</code>, <code>ends(x)</code>,{" "}
            <code>concat(x, y)</code>, <code>star(x)</code>. Regexes go between slashes: <code>/[0-9]+/</code>.
          </p>
          {showNames && (
            <Field label="Custom names (one per line: name = regex)">
              <TextArea
                mono
                rows={2}
                value={namesText}
                className={cx("text-xs")}
                onChange={(e) => {
                  setNamesText(e.target.value);
                  onChange({ ...value, names: parseNames(e.target.value) });
                }}
                placeholder="code = [A-Z]{2}[0-9]{2}"
              />
            </Field>
          )}
        </div>
      )}
    </div>
  );
}
