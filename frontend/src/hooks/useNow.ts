import { useEffect, useState } from "react";

export type Clock = () => number;

/**
 * A small, injectable clock for time-sensitive controls. The injected clock keeps
 * expiry behaviour deterministic in tests while production refreshes once a second.
 */
export function useNow(intervalMs = 1_000, clock: Clock = Date.now): number {
  const [now, setNow] = useState(() => clock());

  useEffect(() => {
    setNow(clock());
    const timer = window.setInterval(() => setNow(clock()), intervalMs);
    return () => window.clearInterval(timer);
  }, [clock, intervalMs]);

  return now;
}
