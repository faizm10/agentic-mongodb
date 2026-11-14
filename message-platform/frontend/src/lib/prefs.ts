"use client";

import { useCallback, useEffect, useState } from "react";

export type ThemePref = "light" | "dark" | "system";

const THEME_KEY = "novaconnect.theme";
const USER_KEY = "novaconnect.demoUser";

function read(storage: () => Storage, key: string): string | null {
  try {
    return storage().getItem(key);
  } catch {
    return null;
  }
}

function write(storage: () => Storage, key: string, value: string) {
  try {
    storage().setItem(key, value);
  } catch {
    /* storage unavailable: preference lasts for this page only */
  }
}

export function applyTheme(pref: ThemePref) {
  const dark = pref === "dark" || (pref === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}

/** Inline script for <head>: applies the saved theme before first paint (no flash). */
export const THEME_BOOTSTRAP = `(function(){try{var p=localStorage.getItem("${THEME_KEY}")||"system";var d=p==="dark"||(p==="system"&&matchMedia("(prefers-color-scheme: dark)").matches);if(d)document.documentElement.classList.add("dark")}catch(e){}})()`;

export function useTheme() {
  const [theme, setThemeState] = useState<ThemePref>("system");

  useEffect(() => {
    const saved = read(() => localStorage, THEME_KEY) as ThemePref | null;
    setThemeState(saved === "light" || saved === "dark" ? saved : "system");
  }, []);

  useEffect(() => {
    applyTheme(theme);
    if (theme !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => applyTheme("system");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [theme]);

  const setTheme = useCallback((next: ThemePref) => {
    write(() => localStorage, THEME_KEY, next);
    setThemeState(next);
  }, []);

  return { theme, setTheme };
}

/**
 * Demo identity is per *tab* (sessionStorage) so two windows can be two different people.
 * `?as=u_sarah` in the URL wins; the last choice is remembered as the default for new tabs.
 */
export function loadDemoUser(): string | null {
  const fromUrl = new URLSearchParams(window.location.search).get("as");
  if (fromUrl) return fromUrl;
  return read(() => sessionStorage, USER_KEY) ?? read(() => localStorage, USER_KEY);
}

export function saveDemoUser(id: string) {
  write(() => sessionStorage, USER_KEY, id);
  write(() => localStorage, USER_KEY, id);
  const url = new URL(window.location.href);
  url.searchParams.set("as", id);
  window.history.replaceState(null, "", url.toString());
}

export function useBoolPref(key: string, initial: boolean) {
  const [value, setValue] = useState(initial);
  useEffect(() => {
    const saved = read(() => localStorage, key);
    if (saved !== null) setValue(saved === "1");
  }, [key]);
  const update = useCallback(
    (next: boolean) => {
      write(() => localStorage, key, next ? "1" : "0");
      setValue(next);
    },
    [key],
  );
  return [value, update] as const;
}
