import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Trader Dashboard",
  description: "Live multi-strategy trading dashboard with real-time portfolio tracking",
};

function Navbar() {
  return (
    <nav className="border-b border-border bg-surface/80 backdrop-blur sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-4 h-14 flex items-center gap-8">
        <Link href="/" className="font-bold text-lg tracking-tight">
          <span className="text-accent">Trader</span>Dashboard
        </Link>
        <div className="flex gap-6 text-sm text-gray-400">
          <Link href="/" className="hover:text-white transition">
            Leaderboard
          </Link>
          <Link href="/feed" className="hover:text-white transition">
            Live Feed
          </Link>
          <Link href="/portfolios" className="hover:text-white transition">
            Portfolios
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

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="antialiased">
        <Navbar />
        <main className="max-w-7xl mx-auto px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
