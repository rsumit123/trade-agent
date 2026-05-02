"use client";
import { usePathname } from "next/navigation";

/** Returns "/admin" if the user is on an admin route, else "/app". */
export function useAppPrefix(): "/app" | "/admin" {
  const p = usePathname() || "";
  if (p.startsWith("/admin")) return "/admin";
  return "/app";
}
