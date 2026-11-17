export const STATUS_STYLE: Record<string, string> = {
  NEW: "bg-slate-100 text-slate-700",
  PROCESSING: "bg-blue-100 text-blue-800",
  DRAFT_READY: "bg-emerald-100 text-emerald-800",
  LOW_FIT: "bg-amber-100 text-amber-800",
  FAILED: "bg-red-100 text-red-800",
  APPROVED: "bg-green-100 text-green-800",
  REJECTED: "bg-zinc-200 text-zinc-700",
  SENT: "bg-violet-100 text-violet-800",
};
export const ACTION: Record<string, { path: string; label: string }> = {
  FAILED: { path: "retry", label: "Retry" },
  LOW_FIT: { path: "draft-anyway", label: "Draft anyway" },
};

export function timeAgo(iso: string) {
  const s = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
