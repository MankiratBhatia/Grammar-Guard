import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";

export interface AsyncState<T> {
  data: T | null;
  error: unknown;
  loading: boolean;
}

/** Run an async function; ignores results that arrive after a newer call (no stale overwrites). */
export function useAsync<A extends unknown[], T>(fn: (...args: A) => Promise<T>) {
  const [state, setState] = useState<AsyncState<T>>({ data: null, error: null, loading: false });
  const ticket = useRef(0);
  const run = useCallback(
    async (...args: A) => {
      const mine = ++ticket.current;
      setState((s) => ({ ...s, loading: true, error: null }));
      try {
        const data = await fn(...args);
        if (mine === ticket.current) setState({ data, error: null, loading: false });
        return data;
      } catch (error) {
        if (mine === ticket.current) setState({ data: null, error, loading: false });
        return null;
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );
  return { ...state, run };
}

/** Debounce a changing value. */
export function useDebounced<T>(value: T, ms = 350): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** Built-in constraint names (digit, email, iso_date, ...) fetched once. */
let builtinsCache: Record<string, string> | null = null;
export function useBuiltins(): Record<string, string> {
  const [b, setB] = useState<Record<string, string>>(builtinsCache ?? {});
  useEffect(() => {
    if (builtinsCache) return;
    api
      .builtins()
      .then((r) => {
        builtinsCache = r.builtins;
        setB(r.builtins);
      })
      .catch(() => undefined);
  }, []);
  return b;
}

/** localStorage-backed state that tolerates storage being unavailable. */
export function useStored<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw === null ? initial : (JSON.parse(raw) as T);
    } catch {
      return initial;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* private mode etc. */
    }
  }, [key, value]);
  return [value, setValue] as const;
}

/** Hash-based routing: "#/generate" -> "generate". */
export function useRoute(fallback: string): [string, (r: string) => void] {
  const read = () => window.location.hash.replace(/^#\/?/, "") || fallback;
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const on = () => setRoute(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return [route, (r) => (window.location.hash = `/${r}`)];
}
