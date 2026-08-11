import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export const usd = (micro: number) => `$${(micro / 1e6).toFixed(4)}`;
export const gbp = (pence: number) => `£${(pence / 100).toFixed(2)}`;
export const when = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" }) : "—";
