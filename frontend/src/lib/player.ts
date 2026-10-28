import { useCallback, useEffect, useState } from "react";

/** A step player: position in [0, last], with play/pause that advances on a timer and stops at the end. */
export function usePlayer(last: number, intervalMs = 900) {
  const [i, setI] = useState(0);
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    setI((cur) => Math.min(cur, Math.max(last, 0)));
  }, [last]);
  useEffect(() => {
    if (!playing) return;
    if (i >= last) {
      setPlaying(false);
      return;
    }
    const t = setTimeout(() => setI((cur) => Math.min(cur + 1, last)), intervalMs);
    return () => clearTimeout(t);
  }, [playing, i, last, intervalMs]);

  return {
    i,
    setI: useCallback((n: number) => setI(Math.max(0, Math.min(n, last))), [last]),
    playing,
    toggle: () => {
      if (i >= last) setI(0);
      setPlaying((p) => !p);
    },
    stop: () => setPlaying(false),
  };
}
