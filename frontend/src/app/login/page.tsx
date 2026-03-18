"use client";
import { useState, useEffect, FormEvent } from "react";
import { useRouter } from "next/navigation";

// ── Liquid Gradient Background ──────────────────────────────────────

function LiquidGradient() {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  return (
    <div className="fixed inset-0 overflow-hidden" aria-hidden="true">
      {/* Dark base */}
      <div className="absolute inset-0 bg-[#050510]" />

      {/* SVG filter for liquid metaball merging */}
      <svg className="absolute w-0 h-0">
        <defs>
          <filter id="liquid">
            <feGaussianBlur in="SourceGraphic" stdDeviation="28" result="blur" />
            <feColorMatrix
              in="blur"
              mode="matrix"
              values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 22 -9"
              result="liquid"
            />
            <feComposite in="SourceGraphic" in2="liquid" operator="atop" />
          </filter>
        </defs>
      </svg>

      {/* Animated blobs with liquid filter */}
      <div
        className={`absolute inset-0 transition-opacity duration-[2000ms] ${
          mounted ? "opacity-100" : "opacity-0"
        }`}
        style={{ filter: "url(#liquid)" }}
      >
        {/* Primary indigo blob — slow orbit */}
        <div
          className="absolute rounded-full"
          style={{
            width: "45vmax",
            height: "45vmax",
            background: "radial-gradient(circle at 30% 30%, #6366f1 0%, #4f46e5 40%, transparent 70%)",
            top: "10%",
            left: "15%",
            animation: "blob-drift-1 18s ease-in-out infinite",
            opacity: 0.85,
          }}
        />

        {/* Purple blob — counter-orbit */}
        <div
          className="absolute rounded-full"
          style={{
            width: "40vmax",
            height: "40vmax",
            background: "radial-gradient(circle at 70% 40%, #8b5cf6 0%, #7c3aed 40%, transparent 70%)",
            top: "30%",
            right: "10%",
            animation: "blob-drift-2 22s ease-in-out infinite",
            opacity: 0.75,
          }}
        />

        {/* Teal accent blob — figure-eight */}
        <div
          className="absolute rounded-full"
          style={{
            width: "35vmax",
            height: "35vmax",
            background: "radial-gradient(circle at 50% 50%, #06b6d4 0%, #0891b2 35%, transparent 70%)",
            bottom: "5%",
            left: "25%",
            animation: "blob-drift-3 20s ease-in-out infinite",
            opacity: 0.6,
          }}
        />

        {/* Small profit-green accent */}
        <div
          className="absolute rounded-full"
          style={{
            width: "22vmax",
            height: "22vmax",
            background: "radial-gradient(circle at 50% 50%, #22c55e 0%, #16a34a 30%, transparent 65%)",
            top: "55%",
            right: "25%",
            animation: "blob-drift-4 16s ease-in-out infinite",
            opacity: 0.4,
          }}
        />

        {/* Deep blue anchor blob */}
        <div
          className="absolute rounded-full"
          style={{
            width: "50vmax",
            height: "50vmax",
            background: "radial-gradient(circle at 40% 60%, #1e1b4b 0%, #0c0a2a 50%, transparent 75%)",
            top: "20%",
            left: "30%",
            animation: "blob-drift-5 25s ease-in-out infinite",
            opacity: 0.9,
          }}
        />
      </div>

      {/* Noise overlay for texture */}
      <div
        className="absolute inset-0 opacity-[0.035] mix-blend-overlay"
        style={{
          backgroundImage: `url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E")`,
          backgroundSize: "128px 128px",
        }}
      />

      {/* Subtle vignette */}
      <div
        className="absolute inset-0"
        style={{
          background: "radial-gradient(ellipse at 50% 50%, transparent 40%, rgba(5,5,16,0.6) 100%)",
        }}
      />
    </div>
  );
}

// ── Login Form ──────────────────────────────────────────────────────

export default function LoginPage() {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [focused, setFocused] = useState(false);
  const router = useRouter();

  useEffect(() => {
    if (document.getElementById("__login-fonts")) return;
    const link = document.createElement("link");
    link.id = "__login-fonts";
    link.rel = "stylesheet";
    link.href = "https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap";
    document.head.appendChild(link);
  }, []);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      const res = await fetch("/api/auth", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });

      if (res.ok) {
        router.push("/");
        router.refresh();
      } else {
        setError("Wrong password");
        setPassword("");
      }
    } catch {
      setError("Something went wrong");
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <LiquidGradient />

      {/* Keyframe animations */}
      <style jsx global>{`
        @keyframes blob-drift-1 {
          0%, 100% { transform: translate(0, 0) scale(1) rotate(0deg); }
          25% { transform: translate(8vw, -6vh) scale(1.08) rotate(45deg); }
          50% { transform: translate(-4vw, 8vh) scale(0.95) rotate(90deg); }
          75% { transform: translate(6vw, 3vh) scale(1.04) rotate(135deg); }
        }
        @keyframes blob-drift-2 {
          0%, 100% { transform: translate(0, 0) scale(1) rotate(0deg); }
          25% { transform: translate(-10vw, 5vh) scale(1.06) rotate(-30deg); }
          50% { transform: translate(6vw, -8vh) scale(0.92) rotate(-60deg); }
          75% { transform: translate(-3vw, -4vh) scale(1.1) rotate(-90deg); }
        }
        @keyframes blob-drift-3 {
          0%, 100% { transform: translate(0, 0) scale(1); }
          33% { transform: translate(12vw, -5vh) scale(1.12); }
          66% { transform: translate(-8vw, -10vh) scale(0.88); }
        }
        @keyframes blob-drift-4 {
          0%, 100% { transform: translate(0, 0) scale(1) rotate(0deg); }
          50% { transform: translate(-15vw, -8vh) scale(1.15) rotate(180deg); }
        }
        @keyframes blob-drift-5 {
          0%, 100% { transform: translate(0, 0) scale(1); }
          25% { transform: translate(3vw, 4vh) scale(1.03); }
          50% { transform: translate(-2vw, -3vh) scale(0.98); }
          75% { transform: translate(4vw, -2vh) scale(1.02); }
        }
        @keyframes float-in {
          from { opacity: 0; transform: translateY(16px) scale(0.97); }
          to { opacity: 1; transform: translateY(0) scale(1); }
        }
        @keyframes shimmer {
          0% { background-position: -200% center; }
          100% { background-position: 200% center; }
        }
        @keyframes pulse-ring {
          0% { transform: scale(0.95); opacity: 0.5; }
          50% { transform: scale(1); opacity: 1; }
          100% { transform: scale(0.95); opacity: 0.5; }
        }
      `}</style>

      {/* Login card */}
      <div className="relative min-h-screen flex items-center justify-center px-4 z-10">
        <div
          className="w-full max-w-sm"
          style={{ animation: "float-in 0.8s cubic-bezier(0.16, 1, 0.3, 1) forwards" }}
        >
          {/* Glass card */}
          <div
            className="relative rounded-2xl p-8 backdrop-blur-xl"
            style={{
              background: "rgba(17, 24, 39, 0.45)",
              border: "1px solid rgba(99, 102, 241, 0.15)",
              boxShadow: focused
                ? "0 0 60px rgba(99, 102, 241, 0.15), 0 25px 50px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.05)"
                : "0 25px 50px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.05)",
              transition: "box-shadow 0.5s ease, border-color 0.5s ease",
              borderColor: focused ? "rgba(99, 102, 241, 0.3)" : "rgba(99, 102, 241, 0.15)",
            }}
          >
            {/* Logo area */}
            <div className="text-center mb-8">
              {/* Animated ring behind logo */}
              <div className="relative inline-flex items-center justify-center mb-5">
                <div
                  className="absolute w-16 h-16 rounded-full"
                  style={{
                    background: "conic-gradient(from 0deg, #6366f1, #8b5cf6, #06b6d4, #6366f1)",
                    animation: "pulse-ring 3s ease-in-out infinite",
                    filter: "blur(8px)",
                    opacity: 0.5,
                  }}
                />
                <div
                  className="relative w-14 h-14 rounded-full flex items-center justify-center"
                  style={{
                    background: "linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%)",
                    boxShadow: "0 4px 20px rgba(99, 102, 241, 0.4)",
                  }}
                >
                  <span className="text-white font-bold text-xl" style={{ fontFamily: "'Outfit', sans-serif" }}>
                    H
                  </span>
                </div>
              </div>

              <h1
                className="text-2xl font-bold text-white tracking-tight"
                style={{ fontFamily: "'Outfit', sans-serif" }}
              >
                <span className="bg-gradient-to-r from-[#6366f1] via-[#8b5cf6] to-[#06b6d4] bg-clip-text text-transparent">
                  Henry
                </span>{" "}
                <span className="text-gray-200 font-light">AI Trader</span>
              </h1>
              <p
                className="text-xs text-gray-500 mt-2 tracking-widest uppercase"
                style={{ fontFamily: "'JetBrains Mono', monospace", letterSpacing: "0.2em" }}
              >
                Portfolio Intelligence
              </p>
            </div>

            {/* Form */}
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="relative">
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onFocus={() => setFocused(true)}
                  onBlur={() => setFocused(false)}
                  placeholder="Enter access key"
                  autoFocus
                  className="w-full h-12 px-4 rounded-xl text-white text-sm placeholder:text-gray-600 focus:outline-none transition-all duration-300"
                  style={{
                    fontFamily: "'JetBrains Mono', monospace",
                    background: "rgba(15, 23, 42, 0.6)",
                    border: "1px solid rgba(99, 102, 241, 0.15)",
                    boxShadow: focused
                      ? "0 0 0 3px rgba(99, 102, 241, 0.1), inset 0 1px 0 rgba(255,255,255,0.03)"
                      : "inset 0 1px 0 rgba(255,255,255,0.03)",
                    borderColor: focused ? "rgba(99, 102, 241, 0.4)" : "rgba(99, 102, 241, 0.15)",
                  }}
                />
              </div>

              {error && (
                <div
                  className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm"
                  style={{
                    background: "rgba(239, 68, 68, 0.08)",
                    border: "1px solid rgba(239, 68, 68, 0.2)",
                    color: "#ef4444",
                    fontFamily: "'Outfit', sans-serif",
                    animation: "float-in 0.3s ease forwards",
                  }}
                >
                  <svg className="w-4 h-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 3.75h.008v.008H12v-.008Z" />
                  </svg>
                  {error}
                </div>
              )}

              <button
                type="submit"
                disabled={loading || !password}
                className="relative w-full h-12 rounded-xl text-white text-sm font-semibold overflow-hidden disabled:opacity-30 disabled:cursor-not-allowed transition-all duration-300 group"
                style={{
                  fontFamily: "'Outfit', sans-serif",
                  background: loading
                    ? "linear-gradient(90deg, #4f46e5 0%, #7c3aed 50%, #4f46e5 100%)"
                    : "linear-gradient(135deg, #4f46e5 0%, #6366f1 50%, #7c3aed 100%)",
                  backgroundSize: loading ? "200% 100%" : "100% 100%",
                  animation: loading ? "shimmer 1.5s linear infinite" : "none",
                  boxShadow: !loading && password
                    ? "0 4px 20px rgba(99, 102, 241, 0.35), inset 0 1px 0 rgba(255,255,255,0.1)"
                    : "none",
                }}
              >
                <span className="relative z-10">
                  {loading ? (
                    <span className="flex items-center justify-center gap-2">
                      <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                      </svg>
                      Authenticating
                    </span>
                  ) : (
                    "Enter"
                  )}
                </span>
                {/* Hover glow */}
                {!loading && (
                  <div
                    className="absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity duration-300"
                    style={{
                      background: "linear-gradient(135deg, #6366f1 0%, #8b5cf6 50%, #a78bfa 100%)",
                    }}
                  />
                )}
              </button>
            </form>

            {/* Bottom accent line */}
            <div className="mt-6 flex justify-center">
              <div
                className="h-px w-16 rounded-full"
                style={{
                  background: "linear-gradient(90deg, transparent, rgba(99, 102, 241, 0.4), transparent)",
                }}
              />
            </div>
          </div>

          {/* Version tag */}
          <p
            className="text-center mt-4 text-[10px] tracking-[0.25em] uppercase"
            style={{
              fontFamily: "'JetBrains Mono', monospace",
              color: "rgba(107, 114, 128, 0.4)",
            }}
          >
            v3.8 &middot; encrypted session
          </p>
        </div>
      </div>
    </>
  );
}
