"use client";
import { usePathname } from "next/navigation";
import { Sidebar, MobileTopBar } from "@/components/Sidebar";
import { BottomNav } from "@/components/BottomNav";
import { ToastProvider } from "@/components/Toast";
import { TrialBanner, TrialEndedFooterStrip, TrialEndedModal } from "@/components/Runtime";

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() || "";
  // Marketing + login render full-bleed without app chrome
  const isPublic = pathname === "/" || pathname.startsWith("/login");

  if (isPublic) {
    return (
      <ToastProvider>
        <main className="min-h-screen">{children}</main>
      </ToastProvider>
    );
  }

  return (
    <ToastProvider>
      <Sidebar isOpen={false} onClose={() => {}} />

      {/* Content area: margin-left on desktop via CSS media query, none on mobile */}
      <div className="flex flex-col min-h-screen app-content">
        {/* Mobile top bar: hidden on md+ */}
        <MobileTopBar />
        {/* Trial-related banners — sticky to top of content */}
        <TrialEndedFooterStrip />
        <TrialBanner />
        {/* Main content — pb-bottom-nav reserves space for fixed bottom nav on mobile */}
        <main className="flex-1 overflow-y-auto pb-bottom-nav md:pb-0">
          {children}
        </main>
      </div>

      {/* Mobile bottom nav */}
      <BottomNav />

      {/* Modal — only renders when trial has ended */}
      <TrialEndedModal />
    </ToastProvider>
  );
}
