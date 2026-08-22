import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Terms — DealHunter",
};

export default function TermsPage() {
  return (
    <div className="mx-auto max-w-2xl px-6 py-16">
      <h1 className="text-3xl font-bold tracking-tight mb-2">Terms of Use</h1>
      <p className="text-sm text-muted-foreground mb-8">Last updated: May 2026</p>

      <div className="flex flex-col gap-6 text-sm leading-relaxed text-muted-foreground">
        <div className="flex flex-col gap-3">
          <h2 className="text-base font-semibold text-foreground">
            Informational site
          </h2>
          <p>
            This site is informational and provided as-is during the development phase
            of DealHunter. No products or services are offered
            for purchase. No contracts are formed by visiting this site or submitting
            the waitlist form.
          </p>
        </div>

        <div className="flex flex-col gap-3">
          <h2 className="text-base font-semibold text-foreground">
            Accuracy of information
          </h2>
          <p>
            The project is in active development. Described capabilities, milestones,
            and timelines are subject to change. No warranties are made regarding the
            accuracy, completeness, or timeliness of the information presented.
          </p>
        </div>

        <div className="flex flex-col gap-3">
          <h2 className="text-base font-semibold text-foreground">
            Open-source code
          </h2>
          <p>
            The codebase underlying this site is open-source and available on GitHub.
            Use of that code is governed by the license in the repository, not by these
            terms.
          </p>
        </div>

        <div className="flex flex-col gap-3">
          <h2 className="text-base font-semibold text-foreground">Contact</h2>
          <p>
            Questions can be sent to{" "}
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
