import { type NextRequest, NextResponse } from "next/server";

// A quick redirect for signed-out visitors; the uptime API still checks every session and workspace.
export function middleware(request: NextRequest) {
  if (!request.cookies.has("opsrelay_session")) return NextResponse.redirect(new URL("/sign-in", request.url));
}

// Node.js, not Edge: Vercel Services rejects Edge output.
export const config = { runtime: "nodejs", matcher: ["/((?!sign-in|api/|_next/|icon.svg|favicon.ico).*)"] };
