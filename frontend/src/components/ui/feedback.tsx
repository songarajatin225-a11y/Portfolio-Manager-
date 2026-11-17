import * as React from "react";
import { AlertTriangle, Info, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded bg-surface-3/60", className)} />;
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("size-4 animate-spin text-muted", className)} />;
}

export function EmptyState({ title, description, action, icon }: { title: string; description?: React.ReactNode; action?: React.ReactNode; icon?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-4 py-10 text-center">
      <div className="text-subtle">{icon ?? <Info className="size-6" />}</div>
      <p className="text-sm font-medium">{title}</p>
      {description ? <div className="max-w-md text-xs text-muted">{description}</div> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

export function ErrorNotice({ error, className }: { error: unknown; className?: string }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div className={cn("flex items-start gap-2 rounded-md border border-down/40 bg-down/10 px-3 py-2 text-xs text-down", className)} role="alert">
      <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
      <span>{message}</span>
    </div>
  );
}

export function Notice({ children, tone = "info", className }: { children: React.ReactNode; tone?: "info" | "warn"; className?: string }) {
  return (
    <div
      className={cn(
        "flex items-start gap-2 rounded-md border px-3 py-2 text-xs",
        tone === "warn" ? "border-warn/40 bg-warn/10 text-warn" : "border-info/30 bg-info/10 text-info",
        className,
      )}
    >
      {tone === "warn" ? <AlertTriangle className="mt-0.5 size-3.5 shrink-0" /> : <Info className="mt-0.5 size-3.5 shrink-0" />}
      <div>{children}</div>
    </div>
  );
}
