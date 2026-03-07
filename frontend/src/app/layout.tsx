import type { Metadata } from "next";
import "./globals.css";
import { LayoutShell } from "./layout-shell";

export const metadata: Metadata = {
  title: "Henry AI Trader",
  description: "AI-powered multi-strategy trading dashboard with real-time portfolio tracking",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="antialiased">
        <LayoutShell>{children}</LayoutShell>
      </body>
    </html>
  );
}
