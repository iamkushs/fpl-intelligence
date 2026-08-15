import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "FPL Intelligence",
  description: "Evidence-backed Fantasy Premier League research",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
