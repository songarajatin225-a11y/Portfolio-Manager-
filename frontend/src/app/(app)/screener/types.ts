export interface ScreenFilter {
  field: string;
  op: "gt" | "gte" | "lt" | "lte" | "between" | "eq" | "neq" | "in" | "is_true" | "is_false";
  value?: number | string | string[] | null;
  value2?: number | null;
}

export interface ScreenRequest {
  filters: ScreenFilter[];
  sort: string;
  direction: "asc" | "desc";
  limit: number;
}

export interface ScreenField {
  key: string;
  label: string;
  category: "technical" | "fundamental" | "ai_ml" | "meta";
  type: "number" | "boolean" | "string";
  unit?: string;
}

export interface ScreenResult {
  universe: number;
  matched: number;
  data_mode: string;
  results: {
    instrument_id: number;
    symbol: string;
    name: string;
    sector: string | null;
    metrics_as_of: string;
    values: Record<string, number | boolean | string | null>;
    live: { ltp: number; change_pct: number | null; data_status: string } | null;
  }[];
}
