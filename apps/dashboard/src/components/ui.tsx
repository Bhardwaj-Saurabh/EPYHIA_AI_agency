// shadcn/ui-style primitives (card, badge, table, button) copied in as source
// per the shadcn model - Tailwind-only, no runtime dependency.
import * as React from "react";
import { cn } from "../lib/utils";

export function Card({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("rounded-xl border border-neutral-200 bg-white shadow-sm", className)}
      {...p}
    />
  );
}

export function CardHeader({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("flex flex-col gap-1 p-4 pb-2", className)} {...p} />;
}

export function CardTitle({ className, ...p }: React.HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h3
      className={cn("text-sm font-semibold tracking-tight text-neutral-700", className)}
      {...p}
    />
  );
}

export function CardContent({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-4 pt-2", className)} {...p} />;
}

const badgeTones: Record<string, string> = {
  green: "bg-emerald-100 text-emerald-800",
  gold: "bg-amber-100 text-amber-800",
  red: "bg-red-100 text-red-700",
  gray: "bg-neutral-100 text-neutral-600",
  blue: "bg-sky-100 text-sky-800",
  brand: "bg-brand-soft text-brand",
};

export function Badge({
  tone = "gray",
  className,
  ...p
}: React.HTMLAttributes<HTMLSpanElement> & { tone?: keyof typeof badgeTones }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
        badgeTones[tone],
        className,
      )}
      {...p}
    />
  );
}

export function statusTone(s: string): keyof typeof badgeTones {
  if (["DONE", "executed", "APPROVED", "PAID", "completed", "BRAND_APPROVED"].includes(s))
    return "green";
  if (["FAILED", "failed"].includes(s)) return "red";
  if (s.startsWith("AWAITING") || s === "pending_approval") return "gold";
  if (["IN_PROGRESS", "started", "CREATED"].includes(s)) return "blue";
  return "gray";
}

export function Table({ className, ...p }: React.HTMLAttributes<HTMLTableElement>) {
  return (
    <div className="w-full overflow-x-auto">
      <table className={cn("w-full text-left text-sm", className)} {...p} />
    </div>
  );
}

export function Th({ className, ...p }: React.ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      className={cn(
        "border-b border-neutral-200 px-3 py-2 text-xs font-semibold uppercase tracking-wide text-neutral-500",
        className,
      )}
      {...p}
    />
  );
}

export function Td({ className, ...p }: React.TdHTMLAttributes<HTMLTableCellElement>) {
  return (
    <td className={cn("border-b border-neutral-100 px-3 py-2 align-top", className)} {...p} />
  );
}

export function Button({
  className,
  variant = "primary",
  ...p
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "outline" }) {
  return (
    <button
      className={cn(
        "inline-flex items-center gap-1 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        variant === "primary"
          ? "bg-brand text-white hover:bg-brand/90"
          : "border border-neutral-300 bg-white text-neutral-700 hover:bg-neutral-50",
        className,
      )}
      {...p}
    />
  );
}
