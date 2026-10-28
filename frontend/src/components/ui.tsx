import { type ButtonHTMLAttributes, type ReactNode, useState } from "react";
import { ApiError } from "../api/client";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

export function Card({
  title,
  subtitle,
  actions,
  children,
  className,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cx("rounded-xl border border-line bg-panel shadow-sm", className)}>
      {(title || actions) && (
        <header className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
          <div>
            <h2 className="text-sm font-semibold tracking-tight">{title}</h2>
            {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

type Variant = "primary" | "secondary" | "ghost" | "danger";
const variants: Record<Variant, string> = {
  primary: "bg-accent text-accent-fg hover:opacity-90 disabled:opacity-50",
  secondary: "border border-line bg-panel2 text-ink hover:border-accent disabled:opacity-50",
  ghost: "text-muted hover:text-ink hover:bg-panel2 disabled:opacity-50",
  danger: "bg-bad text-white hover:opacity-90 disabled:opacity-50",
};

export function Button({
  variant = "secondary",
  className,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      type="button"
      {...rest}
      className={cx(
        "inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed",
        variants[variant],
        className,
      )}
    />
  );
}

export function Field({
  label,
  hint,
  children,
  className,
}: {
  label: string;
  hint?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <label className={cx("block", className)}>
      <span className="mb-1 block text-xs font-medium text-muted">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-muted">{hint}</span>}
    </label>
  );
}

const inputBase =
  "w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink placeholder:text-muted/70 focus:border-accent";

export function TextInput({ className, mono, ...rest }: React.InputHTMLAttributes<HTMLInputElement> & { mono?: boolean }) {
  return <input {...rest} className={cx(inputBase, mono && "font-mono", className)} spellCheck={false} />;
}

export function TextArea({
  className,
  mono,
  ...rest
}: React.TextareaHTMLAttributes<HTMLTextAreaElement> & { mono?: boolean }) {
  return <textarea {...rest} className={cx(inputBase, "resize-y", mono && "font-mono", className)} spellCheck={false} />;
}

export function Segmented<T extends string>({
  value,
  onChange,
  options,
  label,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string }[];
  label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-lg border border-line bg-panel2 p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={cx(
            "rounded-md px-3 py-1 text-xs font-medium transition",
            value === o.value ? "bg-panel text-ink shadow-sm" : "text-muted hover:text-ink",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

type Tone = "neutral" | "good" | "bad" | "warn" | "accent";
const tones: Record<Tone, string> = {
  neutral: "bg-panel2 text-muted",
  good: "bg-good-soft text-good",
  bad: "bg-bad-soft text-bad",
  warn: "bg-warn-soft text-warn",
  accent: "bg-accent-soft text-accent",
};

export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={cx("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium", tones[tone])}>{children}</span>;
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="rounded-lg border border-line bg-panel2/60 px-3 py-2" title={hint}>
      <div className="text-[11px] uppercase tracking-wide text-muted">{label}</div>
      <div className="mt-0.5 text-lg font-semibold tabular-nums leading-tight">{value}</div>
    </div>
  );
}

export function Spinner({ label = "Working…" }: { label?: string }) {
  return (
    <span role="status" className="inline-flex items-center gap-2 text-sm text-muted">
      <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-line border-t-accent" />
      {label}
    </span>
  );
}

/** Shows an API error with its machine code and, for syntax errors, the position. */
export function ErrorBox({ error, source }: { error: unknown; source?: string }) {
  if (!error) return null;
  const e = error instanceof ApiError ? error : null;
  const message = e ? e.message : error instanceof Error ? error.message : String(error);
  const pos = e?.position;
  return (
    <div role="alert" className="rounded-lg border border-bad/40 bg-bad-soft px-3 py-2 text-sm text-bad">
      <div className="flex flex-wrap items-center gap-2">
        {e && <span className="rounded bg-bad/15 px-1.5 py-0.5 font-mono text-[11px]">{e.code}</span>}
        <span>{message}</span>
      </div>
      {source !== undefined && pos !== null && pos !== undefined && pos <= source.length && (
        <pre className="mt-2 overflow-x-auto font-mono text-xs text-ink/80">
          {source}
          {"\n"}
          {" ".repeat(pos)}^
        </pre>
      )}
    </div>
  );
}

export function CodeBlock({ children, copy = true, className }: { children: string; copy?: boolean; className?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className={cx("relative", className)}>
      <pre className="max-h-96 overflow-auto rounded-lg border border-line bg-bg p-3 font-mono text-xs leading-relaxed">{children}</pre>
      {copy && (
        <button
          type="button"
          className="absolute right-2 top-2 rounded border border-line bg-panel px-2 py-0.5 text-[11px] text-muted hover:text-ink"
          onClick={() => {
            void navigator.clipboard?.writeText(children).then(() => {
              setCopied(true);
              setTimeout(() => setCopied(false), 1200);
            });
          }}
        >
          {copied ? "Copied" : "Copy"}
        </button>
      )}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-dashed border-line px-4 py-8 text-center text-sm text-muted">{children}</div>;
}

/** Visible, readable form of a string for display: spaces and control characters made visible. */
export function visible(s: string): string {
  if (s === "") return "ε (empty string)";
  return s.replace(/ /g, "␣").replace(/\n/g, "↵").replace(/\t/g, "⇥");
}

export function downloadText(filename: string, text: string, type = "application/json") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
