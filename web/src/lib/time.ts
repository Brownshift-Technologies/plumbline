/** Small time-formatting helpers shared by Home, Runs and RunDetail. */

export function greeting(now: Date = new Date()): string {
  const h = now.getHours();
  if (h < 5) return "Good evening";
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

export function relativeTime(epochSeconds: number, now: Date = new Date()): string {
  const deltaMs = now.getTime() - epochSeconds * 1000;
  const deltaS = Math.max(0, Math.round(deltaMs / 1000));
  if (deltaS < 60) return "just now";
  const deltaMin = Math.round(deltaS / 60);
  if (deltaMin < 60) return `${deltaMin} min ago`;
  const deltaH = Math.round(deltaMin / 60);
  if (deltaH < 24) return `${deltaH} hour${deltaH === 1 ? "" : "s"} ago`;
  const deltaD = Math.round(deltaH / 24);
  if (deltaD === 1) return "Yesterday";
  return `${deltaD} days ago`;
}

export function formatDuration(ms: number): string {
  const totalSeconds = Math.round(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return minutes > 0 ? `${minutes}m ${seconds}s` : `${seconds}s`;
}

export function formatClock(epochSeconds: number): string {
  const d = new Date(epochSeconds * 1000);
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}
