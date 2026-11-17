import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { Analytics } from "@vercel/analytics/next";
import { AssistantChat } from "@/components/assistant-chat";
import { Mascot } from "@/components/mascot";
import "./globals.css";

const sans = Geist({ subsets: ["latin"], variable: "--font-sans", display: "swap" });
const mono = Geist_Mono({ subsets: ["latin"], variable: "--font-mono", display: "swap" });

export const metadata: Metadata = {
  title: "OpsRelay Status",
  description: "Monitor service uptime, investigate incidents, and track recovery with OpsRelay.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`}>
      <body>
        {children}
        <Mascot />
        <AssistantChat />
        <Analytics />
      </body>
    </html>
  );
}
