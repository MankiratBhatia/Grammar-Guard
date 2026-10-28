import { type ComponentType, lazy, Suspense, useEffect, useState } from "react";
import { api } from "./api/client";
import type { HealthResponse } from "./api/types";
import { Spinner, cx } from "./components/ui";
import { useRoute, useStored } from "./lib/hooks";

const pages: { id: string; label: string; hint: string; icon: string; Page: React.LazyExoticComponent<ComponentType> }[] = [
  { id: "studio", label: "Studio", hint: "Regex → ε-NFA → DFA → minimal DFA", icon: "◇", Page: lazy(() => import("./pages/Studio")) },
  { id: "generate", label: "Generate", hint: "Constrained vs unconstrained GPT-2", icon: "✦", Page: lazy(() => import("./pages/Generate")) },
  { id: "algebra", label: "Constraint Algebra", hint: "Combine rules with ∩ ∪ − ¬", icon: "∪", Page: lazy(() => import("./pages/Algebra")) },
  { id: "validators", label: "Validator Lab", hint: "Diff validators, synthesize tests", icon: "⚖", Page: lazy(() => import("./pages/ValidatorLab")) },
  { id: "json", label: "JSON & Schema", hint: "Pushdown automaton with a stack", icon: "{ }", Page: lazy(() => import("./pages/JsonSchema")) },
  { id: "learn", label: "Learn", hint: "Step through the algorithms", icon: "▶", Page: lazy(() => import("./pages/Learn")) },
];

type Theme = "system" | "light" | "dark";

function useTheme() {
  const [theme, setTheme] = useStored<Theme>("gg-theme", "system");
  useEffect(() => {
    const el = document.documentElement;
    if (theme === "system") el.removeAttribute("data-theme");
    else el.setAttribute("data-theme", theme);
  }, [theme]);
  return [theme, setTheme] as const;
}

/** Polls the health endpoint: shows whether the server is up and the language model has loaded. */
function ServerStatus() {
  const [health, setHealth] = useState<HealthResponse | null | "down">(null);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const h = await api.health();
        if (alive) setHealth(h);
        timer = setTimeout(poll, h.engine.loaded ? 15000 : 2000);
      } catch {
        if (alive) setHealth("down");
        timer = setTimeout(poll, 4000);
      }
    };
    void poll();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, []);
  const engine = health && health !== "down" ? health.engine : null;
  const [dot, text] =
    health === "down" ? ["bg-bad", "Server offline"] : health === null ? ["bg-muted", "Connecting…"] : engine?.loaded ? ["bg-good", `${engine.model} ready${engine.busy ? " · busy" : ""}`] : ["bg-warn animate-pulse", `Loading ${engine?.model ?? "model"}…`];
  return (
    <div className="flex items-center gap-2 text-xs text-muted" role="status" aria-live="polite">
      <span className={cx("h-2 w-2 rounded-full", dot)} />
      {text}
    </div>
  );
}

export default function App() {
  const [route, go] = useRoute("studio");
  const [theme, setTheme] = useTheme();
  const current = pages.find((p) => p.id === route) ?? pages[0];
  const Page = current.Page;

  return (
    <div className="flex h-full flex-col md:flex-row">
      <aside className="flex shrink-0 flex-col border-b border-line bg-panel md:w-60 md:border-b-0 md:border-r">
        <div className="flex items-center gap-2.5 px-4 py-4">
          <img src="/favicon.svg" alt="" className="h-7 w-7" />
          <div>
            <div className="text-[15px] font-semibold leading-tight tracking-tight">GrammarGuard</div>
            <div className="text-[11px] leading-tight text-muted">Formal guarantees for LLM output</div>
          </div>
        </div>
        <nav aria-label="Main" className="flex gap-1 overflow-x-auto px-2 pb-2 md:flex-1 md:flex-col md:overflow-visible md:pb-0">
          {pages.map((p) => (
            <a
              key={p.id}
              href={`#/${p.id}`}
              onClick={(e) => {
                e.preventDefault();
                go(p.id);
              }}
              aria-current={p.id === current.id ? "page" : undefined}
              className={cx(
                "flex shrink-0 items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition",
                p.id === current.id ? "bg-accent-soft font-medium text-accent" : "text-muted hover:bg-panel2 hover:text-ink",
              )}
            >
              <span className="w-5 text-center font-mono text-xs" aria-hidden>
                {p.icon}
              </span>
              <span className="whitespace-nowrap">{p.label}</span>
            </a>
          ))}
        </nav>
        <div className="hidden items-center justify-between gap-2 border-t border-line px-4 py-3 md:flex md:flex-col md:items-start">
          <ServerStatus />
          <label className="flex items-center gap-2 text-xs text-muted">
            Theme
            <select
              value={theme}
              onChange={(e) => setTheme(e.target.value as Theme)}
              className="rounded border border-line bg-panel2 px-1.5 py-0.5 text-xs text-ink"
              aria-label="Theme"
            >
              <option value="system">System</option>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </select>
          </label>
        </div>
      </aside>
      <main className="min-w-0 flex-1 overflow-y-auto" id="main">
        <div className="mx-auto max-w-6xl px-4 py-5 md:px-8 md:py-7">
          <header className="mb-5">
            <h1 className="text-xl font-semibold tracking-tight">{current.label}</h1>
            <p className="text-sm text-muted">{current.hint}</p>
          </header>
          <Suspense fallback={<Spinner label="Loading page…" />}>
            <Page />
          </Suspense>
        </div>
      </main>
    </div>
  );
}
