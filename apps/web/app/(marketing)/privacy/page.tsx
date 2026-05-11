import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Privacy — Agentic Travel Booking System",
};

const GITHUB_URL =
  "https://github.com/gaurav-gandhi-2411/agentic-travel-booking-system";

export default function PrivacyPage() {
  return (
    <div className="mx-auto max-w-2xl px-6 py-16">
      <h1 className="text-3xl font-bold tracking-tight mb-2">Privacy</h1>
      <p className="text-sm text-muted-foreground mb-8">Last updated: May 2026</p>

      <div className="flex flex-col gap-6 text-sm leading-relaxed text-muted-foreground">
        <div className="flex flex-col gap-3">
          <h2 className="text-base font-semibold text-foreground">
            What we collect
          </h2>
          <p>
            This site does not currently collect any personal data. The waitlist form
            does not store submissions — it is a styled placeholder. No email addresses
            are retained.
          </p>
          <p>
            This page will be updated with a full privacy policy before any data
            collection begins. That update will happen no later than Phase 8, when
            backend services and user accounts are introduced.
          </p>
        </div>

        <div className="flex flex-col gap-3">
          <h2 className="text-base font-semibold text-foreground">
            Analytics and tracking
          </h2>
          <p>
            No analytics, tracking pixels, or third-party scripts are loaded by this
            site. There are no cookies set by this domain.
          </p>
        </div>

        <div className="flex flex-col gap-3">
          <h2 className="text-base font-semibold text-foreground">Transparency</h2>
          <p>
            The project is open-source. You can review the full codebase — including
            this site — to verify the absence of data collection:
          </p>
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="text-foreground font-medium hover:underline underline-offset-4 w-fit"
          >
            github.com/gaurav-gandhi-2411/agentic-travel-booking-system →
          </a>
        </div>

        <div className="flex flex-col gap-3">
          <h2 className="text-base font-semibold text-foreground">Contact</h2>
          <p>
            Questions about privacy can be sent to{" "}
            <a
              href="mailto:gaurav.gandhi.2411@gmail.com"
              className="text-foreground font-medium hover:underline underline-offset-4"
            >
              gaurav.gandhi.2411@gmail.com
            </a>
            .
          </p>
        </div>

        <p className="pt-2 border-t border-border/50">
          Back to{" "}
          <Link
            href="/"
            className="text-foreground font-medium hover:underline underline-offset-4"
          >
            home
          </Link>
          .
        </p>
      </div>
    </div>
  );
}
