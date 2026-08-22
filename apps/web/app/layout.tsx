import type { Metadata } from "next";
import { Inter } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const inter = Inter({ subsets: ["latin"] });

const DESCRIPTION =
  "The reasoning layer for travel platforms. Multi-agent window optimization, two-archetype ranking, and conversational refinement — built as a B2B SDK.";

export const metadata: Metadata = {
  metadataBase: new URL(
    process.env.VERCEL_URL
      ? `https://${process.env.VERCEL_URL}`
      : "http://localhost:3000",
  ),
  title: "DealHunter",
  description: DESCRIPTION,
  openGraph: {
    title: "DealHunter",
    description: DESCRIPTION,
    type: "website",
    siteName: "DealHunter",
  },
  twitter: {
    card: "summary_large_image",
    title: "DealHunter",
    description: DESCRIPTION,
  },
};

const GITHUB_URL =
  "https://github.com/gaurav-gandhi-2411/agentic-travel-booking-system";
const CONTACT_EMAIL = "gaurav.gandhi.2411@gmail.com";

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className={`${inter.className} min-h-screen flex flex-col`}>
        <header className="border-b border-border/60 bg-background/95 sticky top-0 z-50 backdrop-blur-sm">
          <div className="mx-auto max-w-5xl px-6 py-4 flex items-center justify-between">
            <Link
              href="/"
              className="font-semibold text-sm tracking-tight text-foreground hover:text-foreground/80 transition-colors"
            >
              DealHunter
            </Link>
            <nav className="flex items-center gap-6 text-sm">
              <Link
                href="/about"
                className="text-muted-foreground hover:text-foreground transition-colors"
              >
                About
              </Link>
              <a
                href={GITHUB_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="text-muted-foreground hover:text-foreground transition-colors"
              >
                GitHub
              </a>
            </nav>
          </div>
        </header>

        <main className="flex-1">{children}</main>

        <footer className="border-t border-border/60 bg-background">
          <div className="mx-auto max-w-5xl px-6 py-8 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 text-sm text-muted-foreground">
            <div className="flex flex-col gap-1">
              <p>© {new Date().getFullYear()} Gaurav Gandhi. Building in public.</p>
              <a
                href={`mailto:${CONTACT_EMAIL}`}
                className="hover:text-foreground transition-colors"
              >
                {CONTACT_EMAIL}
              </a>
            </div>
            <nav className="flex items-center gap-6">
              <Link href="/about" className="hover:text-foreground transition-colors">
                About
              </Link>
              <a
                href={GITHUB_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="hover:text-foreground transition-colors"
              >
                GitHub
              </a>
              <Link href="/privacy" className="hover:text-foreground transition-colors">
                Privacy
              </Link>
              <Link href="/terms" className="hover:text-foreground transition-colors">
                Terms
              </Link>
            </nav>
          </div>
        </footer>
      </body>
    </html>
  );
}
