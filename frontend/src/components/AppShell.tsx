"use client";
import { useState, useCallback } from "react";
import { Sidebar, MobileTopBar } from "@/components/Sidebar";
import { ToastProvider } from "@/components/Toast";

export function AppShell({ children }: { children: React.ReactNode }) {
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const openSidebar = useCallback(() => setSidebarOpen(true), []);
  const closeSidebar = useCallback(() => setSidebarOpen(false), []);

  return (
    <ToastProvider>
      <Sidebar isOpen={sidebarOpen} onClose={closeSidebar} />

      {/* Content area: margin-left on desktop via CSS media query, none on mobile */}
      <div className="flex flex-col min-h-screen app-content">
        {/* Mobile top bar: hidden on md+ via the component itself */}
        <MobileTopBar onMenuClick={openSidebar} />
        {/* Main content */}
        <main className="flex-1 overflow-y-auto">
          {children}
        </main>
      </div>
    </ToastProvider>
  );
}
