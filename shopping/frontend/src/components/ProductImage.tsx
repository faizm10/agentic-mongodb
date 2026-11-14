"use client";

/* eslint-disable @next/next/no-img-element */
import { useState } from "react";
import { img } from "@/lib/format";

type Props = {
  src: string;
  alt: string;
  width: number;
  height?: number;
  zoom?: number;
  fx?: number;
  fy?: number;
  className?: string;
  priority?: boolean;
};

export function ProductImage({ src, alt, width, height, zoom, fx, fy, className = "", priority }: Props) {
  const [failed, setFailed] = useState(false);
  if (failed || !src) {
    return (
      <div
        role="img"
        aria-label={alt}
        className={`flex items-center justify-center bg-gradient-to-br from-nova-100 via-ink-50 to-ember-400/20 ${className}`}
      >
        <span className="font-display text-4xl text-nova-700/60">{alt.slice(0, 1)}</span>
      </div>
    );
  }
  return (
    <img
      src={img(src, width, { height, zoom, fx, fy })}
      alt={alt}
      loading={priority ? "eager" : "lazy"}
      decoding="async"
      onError={() => setFailed(true)}
      className={`object-cover ${className}`}
    />
  );
}
