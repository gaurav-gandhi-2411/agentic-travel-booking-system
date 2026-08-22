import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "About — DealHunter",
  description:
    "Solo developer building a multi-agent reasoning layer for travel platforms. Based in India, building in public.",
};

const GITHUB_URL =
  "https://github.com/gaurav-gandhi-2411/agentic-travel-booking-system";

export default function AboutPage() {
  return (
    <div className="mx-auto max-w-2xl px-6 py-16">
      <h1 className="text-3xl font-bold tracking-tight mb-8">About</h1>

      <div className="flex flex-col gap-6 text-sm leading-relaxed text-muted-foreground">
        <p className="text-base text-foreground">
          I&apos;m Gaurav Gandhi — a solo developer based in India, building a multi-agent
          reasoning layer for travel platforms.
        </p>

        <div className="flex flex-col gap-3">
          <h2 className="text-base font-semibold text-foreground">The project</h2>
          <p>
            DealHunter is a B2B SDK designed to sit in front of
            existing travel inventory. Platforms — OTAs, metasearch engines, booking
            applications — have flights and hotels. What they typically lack is a reasoning
            agent that identifies optimal booking windows across a 30-day horizon, ranks
            packages on genuine value-versus-experience tradeoffs with natural-language
            explanations, and refines conversationally without losing context across turns.
          </p>
          <p>
            The architecture follows the adapter pattern: you plug in your inventory; the
            agent handles window search, scoring, ranking, explanation, and refinement
            routing. The system targets fine-tuned Qwen 2.5 7B and 14B models,
            benchmarked against 70B frontier baselines, with a reproducible eval harness
            and published results.
          </p>
        </div>

        <div className="flex flex-col gap-3">
          <h2 className="text-base font-semibold text-foreground">Where things stand</h2>
          <p>
            Phase 0.5 is this marketing site. The underlying architecture is documented
            across 14 ADRs and being implemented phase by phase. Provider adapters and
            agents are in progress. Fine-tuning begins after the eval harness and baseline
            benchmarks are in place. The full booking flow and chat frontend are later
            phases.
          </p>
          <p>No team, no funding, no investors. Solo project, building in public.</p>
        </div>

        <div className="flex flex-col gap-3">
          <h2 className="text-base font-semibold text-foreground">Resources</h2>
          <ul className="flex flex-col gap-2">
            <li>
              <a
                href={`${GITHUB_URL}/blob/main/docs/customer/value-proposition.md`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-foreground font-medium hover:underline underline-offset-4"
              >
                Value proposition
              </a>{" "}
              — four USP claims, each backed by a verifiable artifact
            </li>
            <li>
              <a
                href={`${GITHUB_URL}/tree/main/docs/research`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-foreground font-medium hover:underline underline-offset-4"
              >
                Technical report
              </a>{" "}
              — placeholder; publishes with the research track in Phase 11.5
            </li>
            <li>
              <a
                href={`${GITHUB_URL}/tree/main/docs/architecture/adr`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-foreground font-medium hover:underline underline-offset-4"
              >
                ADRs 0001–0014
              </a>{" "}
              — every architectural decision documented with rationale and alternatives
            </li>
          </ul>
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
