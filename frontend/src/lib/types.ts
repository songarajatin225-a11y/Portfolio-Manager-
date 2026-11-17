export type DataStatus = "LIVE" | "DELAYED" | "HISTORICAL" | "SIMULATED";

export interface Instrument {
  id: number;
  symbol: string;
  exchange: string;
  segment: string;
  name: string;
  isin: string | null;
  instrument_type: string;
  lot_size: number;
  tick_size: number;
  expiry: string | null;
  strike: number | null;
  option_type: string | null;
  sector: string | null;
  industry: string | null;
  is_index: boolean;
  is_simulated: boolean;
}

export interface Quote {
  instrument_id: number | null;
  provider_key: string;
  symbol: string | null;
  ltp: number;
  open: number | null;
  high: number | null;
  low: number | null;
  prev_close: number | null;
  change: number | null;
  change_pct: number | null;
  volume: number | null;
  vwap: number | null;
  bid: number | null;
  ask: number | null;
  bid_qty: number | null;
  ask_qty: number | null;
  oi: number | null;
  high_52w: number | null;
  low_52w: number | null;
  upper_circuit: number | null;
  lower_circuit: number | null;
  depth_buy: { price: number; quantity: number; orders: number | null }[];
  depth_sell: { price: number; quantity: number; orders: number | null }[];
  received_at: string;
  exchange_ts: string | null;
  last_trade_time: string | null;
  source: string;
  is_simulated: boolean;
  data_status: DataStatus;
  is_stale: boolean;
  age_seconds: number | null;
}

export interface InstrumentWithQuote {
  instrument: Instrument;
  quote: Quote | null;
}

export interface Candle {
  ts: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  oi: number | null;
}

export interface CandleResponse {
  instrument: Instrument;
  interval: string;
  data_status: DataStatus;
  source: string;
  fetched_from_provider: boolean;
  warnings: string[];
  as_of: string | null;
  candles: Candle[];
}
