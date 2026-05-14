import type { Metadata } from 'next';
import DemoClient from '@/components/demo/DemoClient';

export const metadata: Metadata = {
  title: 'DealHunter — Agentic Travel Search',
  description: 'Watch AI agents search, rank, and explain your best flight options in real time.',
};

export default function DemoPage() {
  return <DemoClient />;
}
