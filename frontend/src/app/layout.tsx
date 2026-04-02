import type { Metadata } from "next";
import "./globals.css";
import { AppShell } from "@/components/AppShell";

export const metadata: Metadata = {
  title: "AlphaAgent — AI Paper Trading",
  description: "Multi-session AI paper trading agent dashboard",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="h-full">
      <body className="h-full bg-bg-primary text-text-primary">
        <AppShell>
          {children}
        </AppShell>
      </body>
    </html>
  );
}
