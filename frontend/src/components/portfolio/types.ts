import type { DataStatus } from "@/lib/types";

export interface PortfolioLite {
  id: number;
  name: string;
  description: string;
  benchmark_symbol: string;
  target_allocations: Record<string, number>;
  transaction_count: number;
  current_value: number;
  total_pnl: number;
  xirr_pct: number | null;
  holdings: number;
}

export interface Holding {
  instrument_id: number;
  symbol: string;
  name: string;
  sector: string;
  category: string;
  quantity: number;
  avg_cost: number;
  invested: number;
  price: number | null;
  market_value: number;
  unrealized_pnl: number;
  unrealized_pct: number | null;
  realized_pnl: number;
  dividends: number;
  weight: number;
  price_source: string | null;
  price_status: DataStatus | null;
  price_as_of: string | null;
}

export interface Allocation {
  name: string;
  value: number;
  weight: number;
}

export interface PortfolioSummary {
  portfolio: { id: number; name: string; description: string; benchmark_symbol: string; target_allocations: Record<string, number> };
  current_value: number;
  invested_in_holdings: number;
  total_invested: number;
  unrealized_pnl: number;
  realized_pnl: number;
  dividends: number;
  total_pnl: number;
  absolute_return_pct: number | null;
  xirr_pct: number | null;
  holdings: Holding[];
  allocation_by_sector: Allocation[];
  allocation_by_category: Allocation[];
  contribution: { symbol: string; pnl: number; share: number | null }[];
  concentration: { hhi: number | null; effective_holdings: number | null; top5_weight: number | null; largest: { name: string; weight: number } | null };
  missing_prices: string[];
  note: string | null;
  transaction_count: number;
  data_mode: "real" | "simulated";
  price_statuses: DataStatus[];
  first_trade_date: string | null;
}

export interface Performance {
  available: boolean;
  reason?: string;
  twr_pct: number | null;
  cagr_pct: number | null;
  volatility_pct: number | null;
  max_drawdown_pct: number | null;
  current_drawdown_pct?: number;
  benchmark_return_pct?: number;
  excess_return_pct?: number;
  beta?: number;
  benchmark: string | null;
  data_status: DataStatus;
  methodology: string;
  missing_history: string[];
  series: { date: string; value: number; index: number; benchmark: number | null; drawdown: number }[];
}

export interface TransactionRow {
  id: number;
  instrument_id: number;
  symbol: string;
  exchange: string;
  name: string;
  side: "BUY" | "SELL" | "DIVIDEND";
  quantity: number;
  price: number;
  charges: number;
  trade_date: string;
  category: string;
  notes: string;
  value: number;
}
