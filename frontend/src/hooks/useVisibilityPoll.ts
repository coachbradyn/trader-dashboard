"use client";
/**
 * useVisibilityPoll
 * =================
 * setInterval, but paused when the tab is hidden. Also re-fires the
 * callback immediately on becoming-visible so a backgrounded user
 * doesn't stare at stale data for up to `intervalMs`.
 *
 * Why not just setInterval: setInterval keeps running when the tab is
 * hidden, which — for a dashboard with 30s polls across several
 * components — is 120+ unnecessary requests/hour per tab. Browsers
 * throttle background timers but they don't stop them. document
 * visibility is the right signal.
 */
import { useEffect, useRef } from "react";

export function useVisibilityPoll(callback: () => void | Promise<void>, intervalMs: number) {
  // Store the latest callback in a ref so changing it doesn't tear down
  // the interval. Lets the effect depend only on intervalMs.
  const cbRef = useRef(callback);
  useEffect(() => {
    cbRef.current = callback;
  }, [callback]);

  useEffect(() => {
    let id: ReturnType<typeof setInterval> | null = null;

    const start = () => {
      if (id != null) return;
      id = setInterval(() => {
        void cbRef.current();
      }, intervalMs);
    };

    const stop = () => {
      if (id != null) {
        clearInterval(id);
        id = null;
      }
    };

    const onVisibility = () => {
      if (document.visibilityState === "visible") {
        // Catch up immediately when the user returns, then resume polling.
        void cbRef.current();
        start();
      } else {
        stop();
      }
    };

    // Initial state
    if (document.visibilityState === "visible") start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      stop();
    };
  }, [intervalMs]);
}
