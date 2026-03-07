import { NextResponse } from "next/server";

const SITE_PASSWORD = "BuiltDifferent";
const COOKIE_NAME = "henry-auth";

export async function POST(request: Request) {
  const { password } = await request.json();

  if (password === SITE_PASSWORD) {
    const response = NextResponse.json({ success: true });
    response.cookies.set(COOKIE_NAME, SITE_PASSWORD, {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "lax",
      maxAge: 60 * 60 * 24 * 30, // 30 days
      path: "/",
    });
    return response;
  }

  return NextResponse.json({ success: false, error: "Wrong password" }, { status: 401 });
}
