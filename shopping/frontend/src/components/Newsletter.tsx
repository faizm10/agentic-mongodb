"use client";

import { useState } from "react";
import { ArrowRight, Check } from "lucide-react";

export function Newsletter() {
  const [email, setEmail] = useState("");
  const [done, setDone] = useState(false);
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (email.includes("@")) setDone(true);
      }}
      className="flex w-full max-w-md gap-2"
    >
      {done ? (
        <p className="flex h-12 items-center gap-2 text-sm font-medium text-white">
          <Check className="h-4 w-4" /> You&apos;re on the list. Welcome to NovaMart.
        </p>
      ) : (
        <>
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            aria-label="Email address"
            className="h-12 flex-1 rounded-full border border-white/20 bg-white/10 px-5 text-sm text-white outline-none placeholder:text-white/50 focus:border-white/50"
          />
          <button className="flex h-12 items-center gap-2 rounded-full bg-white px-5 text-sm font-semibold text-ink-900 transition hover:bg-nova-100">
            Join <ArrowRight className="h-4 w-4" />
          </button>
        </>
      )}
    </form>
  );
}
