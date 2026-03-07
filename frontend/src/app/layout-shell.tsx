"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

function Navbar() {
  return (
    <nav className="border-b border-border bg-surface/80 backdrop-blur sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-4 h-14 flex items-center gap-8">
        <Link href="/" className="font-bold text-lg tracking-tight">
          <span className="text-accent">Henry</span> AI Trader
        </Link>
        <div className="flex gap-6 text-sm text-gray-400">
          <Link href="/leaderboard" className="hover:text-white transition">
            Leaderboard
          </Link>
          <Link href="/feed" className="hover:text-white transition">
            Live Feed
          </Link>
          <Link href="/portfolios" className="hover:text-white transition">
            Portfolios
          </Link>
          <Link
            href="/ai"
            className="hover:text-white transition flex items-center gap-1.5"
          >
            <span className="relative flex h-1.5 w-1.5">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-ai-blue opacity-75" />
              <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-ai-blue" />
            </span>
            AI Analysis
          </Link>
          <Link href="/screener" className="hover:text-white transition flex items-center gap-1.5">
            <span className="relative flex h-1.5 w-1.5">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-500 opacity-75" />
              <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-amber-500" />
            </span>
            Screener
          </Link>
          <Link href="/settings" className="hover:text-white transition">
            Settings
          </Link>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-profit animate-pulse" />
          <span className="text-xs text-gray-500">Live</span>
        </div>
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
      <main className="max-w-7xl mx-auto px-4 py-6">{children}</main>
    </>
  );
}
