"use client";

/**
 * Extension point for stock-page tabs added by later modules (news, ML predictions, AI research).
 * Kept separate so the core research page doesn't depend on those modules.
 */

import type { Instrument } from "@/lib/types";

// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function StockExtraTriggers({ isIndex }: { isIndex: boolean }) {
  return null;
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function StockExtraTabs({ symbol, instrument }: { symbol: string; instrument: Instrument }) {
  return null;
}
