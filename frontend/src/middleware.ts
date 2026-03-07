import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const SITE_PASSWORD = "BuiltDifferent";
const COOKIE_NAME = "henry-auth";

export function middleware(request: NextRequest) {
  // Allow the login API route through
  if (request.nextUrl.pathname === "/api/auth") {
    return NextResponse.next();
  }

  // Allow static files and Next.js internals
  if (
    request.nextUrl.pathname.startsWith("/_next") ||
    request.nextUrl.pathname.startsWith("/favicon") ||
    request.nextUrl.pathname === "/login"
  ) {
    return NextResponse.next();
  }

  // Check auth cookie
  const authCookie = request.cookies.get(COOKIE_NAME);
  if (authCookie?.value === SITE_PASSWORD) {
    return NextResponse.next();
  }

  // Redirect to login
  const loginUrl = new URL("/login", request.url);
  return NextResponse.redirect(loginUrl);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
