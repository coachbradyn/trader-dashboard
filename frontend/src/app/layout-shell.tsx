"use client";
import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Sheet, SheetContent } from "@/components/ui/sheet";

const NAV_LINKS = [
  { href: "/leaderboard", label: "Leaderboard" },
  { href: "/feed", label: "Live Feed" },
  { href: "/portfolios", label: "Portfolios" },
  {
    href: "/ai",
    label: "AI Analysis",
    dot: "bg-ai-blue",
  },
  {
    href: "/screener",
    label: "Screener",
    dot: "bg-amber-500",
  },
  {
    href: "/portfolio-manager",
    label: "Manager",
    dot: "bg-ai-blue",
  },
  { href: "/settings", label: "Settings" },
];

function Navbar() {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();

  return (
    <nav className="border-b border-border bg-surface/80 backdrop-blur sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-3 sm:px-4 h-14 flex items-center gap-4 md:gap-8">
        <Link href="/" className="font-bold text-lg tracking-tight shrink-0">
          <span className="text-accent">Henry</span> AI Trader
        </Link>

        {/* Desktop nav */}
        <div className="hidden md:flex gap-6 text-sm text-gray-400">
          {NAV_LINKS.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className={`hover:text-white transition flex items-center gap-1.5 ${
                pathname === link.href ? "text-white" : ""
              }`}
            >
              {link.dot && (
                <span className="relative flex h-1.5 w-1.5">
                  <span className={`animate-ping absolute inline-flex h-full w-full rounded-full ${link.dot} opacity-75`} />
                  <span className={`relative inline-flex rounded-full h-1.5 w-1.5 ${link.dot}`} />
                </span>
              )}
              {link.label}
            </Link>
          ))}
        </div>

        {/* Desktop live indicator */}
        <div className="ml-auto hidden md:flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-profit animate-pulse" />
          <span className="text-xs text-gray-500">Live</span>
        </div>

        {/* Mobile hamburger */}
        <button
          className="md:hidden ml-auto p-2 -mr-2 text-gray-400 hover:text-white transition"
          onClick={() => setOpen(true)}
          aria-label="Open menu"
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25h16.5" />
          </svg>
        </button>

        {/* Mobile drawer */}
        <Sheet open={open} onOpenChange={setOpen}>
          <SheetContent side="left" className="bg-surface border-r border-border w-72 p-0">
            <div className="px-5 pt-6 pb-4 border-b border-border">
              <Link href="/" className="font-bold text-lg tracking-tight" onClick={() => setOpen(false)}>
                <span className="text-accent">Henry</span> AI Trader
              </Link>
            </div>
            <nav className="flex flex-col py-2">
              {NAV_LINKS.map((link) => (
                <Link
                  key={link.href}
                  href={link.href}
                  onClick={() => setOpen(false)}
                  className={`flex items-center gap-3 px-5 py-3 text-sm transition ${
                    pathname === link.href
                      ? "text-white bg-surface-light/40"
                      : "text-gray-400 hover:text-white hover:bg-surface-light/20"
                  }`}
                >
                  {link.dot && (
                    <span className="relative flex h-1.5 w-1.5">
                      <span className={`animate-ping absolute inline-flex h-full w-full rounded-full ${link.dot} opacity-75`} />
                      <span className={`relative inline-flex rounded-full h-1.5 w-1.5 ${link.dot}`} />
                    </span>
                  )}
                  {link.label}
                </Link>
              ))}
            </nav>
            <div className="absolute bottom-6 left-5 flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-profit animate-pulse" />
              <span className="text-xs text-gray-500">Live</span>
            </div>
          </SheetContent>
        </Sheet>
      </div>
    </nav>
  );
}

export function LayoutShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isLogin = pathname === "/login";

  if (isLogin) {
    return <>{children}</>;
  }

  return (
    <>
      <Navbar />
      <main className="max-w-7xl mx-auto px-3 sm:px-4 py-4 sm:py-6">{children}</main>
    </>
  );
}
