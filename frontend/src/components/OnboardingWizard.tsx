"use client";

import { useState } from "react";
import Link from "next/link";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

const FONT_OUTFIT = { fontFamily: "'Outfit', sans-serif" } as const;
const FONT_MONO = { fontFamily: "'JetBrains Mono', monospace" } as const;

const STEPS = [
  {
    number: 1,
    title: "Create a Portfolio",
    description: "Set up your first portfolio with initial capital and risk parameters.",
    link: "/settings",
    linkLabel: "Go to Settings",
  },
  {
    number: 2,
    title: "Generate an API Key",
    description: "Create an API key that TradingView will use to send signals.",
    link: "/settings",
    linkLabel: "Go to Strategies Tab",
  },
  {
    number: 3,
    title: "Configure TradingView",
    description: "Set up your TradingView alert with the webhook URL and payload format.",
    link: null,
    linkLabel: null,
  },
  {
    number: 4,
    title: "Send a Test Webhook",
    description: "Use the Test Webhook button in Settings to verify everything is connected.",
    link: "/settings",
    linkLabel: "Go to Settings",
  },
];

const WEBHOOK_URL = (typeof window !== "undefined" ? window.location.origin : "https://your-domain.com") + "/api/webhook";

const PAYLOAD_TEMPLATE = `{
  "trader": "{{strategy.order.id}}",
  "key": "YOUR_API_KEY",
  "signal": "entry",
  "ticker": "{{ticker}}",
  "dir": "long",
  "price": {{close}},
  "time": {{timenow}}
}`;

export default function OnboardingWizard() {
  const [activeStep, setActiveStep] = useState(0);

  return (
    <div className="max-w-2xl mx-auto py-12">
      <div className="text-center mb-8">
        <h1 className="text-2xl font-bold text-white mb-2" style={FONT_OUTFIT}>
          Welcome to Henry AI Trader
        </h1>
        <p className="text-sm text-gray-500" style={FONT_OUTFIT}>
          Follow these steps to get your first strategy connected
        </p>
      </div>

      <div className="space-y-3">
        {STEPS.map((step, i) => (
          <Card
            key={step.number}
            className={`cursor-pointer transition-all ${activeStep === i ? "border-[#6366f1]/40 bg-[#6366f1]/[0.04]" : "border-[#374151]/50 bg-[#1f2937]/20 hover:border-[#374151]"}`}
            onClick={() => setActiveStep(i)}
          >
            <CardContent className="p-5">
              <div className="flex items-start gap-4">
                <div className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 text-sm font-bold ${activeStep === i ? "bg-[#6366f1]/20 text-[#6366f1] border border-[#6366f1]/30" : "bg-[#1f2937] text-gray-500 border border-[#374151]"}`} style={FONT_MONO}>
                  {step.number}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <h3 className="text-sm font-semibold text-white" style={FONT_OUTFIT}>{step.title}</h3>
                  </div>
                  <p className="text-xs text-gray-400" style={FONT_OUTFIT}>{step.description}</p>

                  {activeStep === i && step.number === 3 && (
                    <div className="mt-4 space-y-3">
                      <div>
                        <label className="text-[10px] text-gray-500 uppercase tracking-wider block mb-1" style={FONT_OUTFIT}>Webhook URL</label>
                        <div className="flex items-center gap-2">
                          <code className="flex-1 text-[11px] bg-black/40 px-3 py-2 rounded border border-[#374151] text-white select-all break-all" style={FONT_MONO}>{WEBHOOK_URL}</code>
                        </div>
                      </div>
                      <div>
                        <label className="text-[10px] text-gray-500 uppercase tracking-wider block mb-1" style={FONT_OUTFIT}>Payload Format</label>
                        <pre className="text-[10px] bg-black/40 px-3 py-2 rounded border border-[#374151] text-gray-300 overflow-x-auto" style={FONT_MONO}>{PAYLOAD_TEMPLATE}</pre>
                      </div>
                    </div>
                  )}

                  {activeStep === i && step.link && (
                    <div className="mt-3">
                      <Link href={step.link}>
                        <Button size="sm" variant="outline" className="text-xs h-7 bg-[#6366f1]/10 text-[#6366f1] border-[#6366f1]/30 hover:bg-[#6366f1]/20">
                          {step.linkLabel}
                        </Button>
                      </Link>
                    </div>
                  )}
                </div>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
