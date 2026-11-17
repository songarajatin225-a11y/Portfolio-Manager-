"use client";

import { Select } from "@/components/ui/input";
import type { PortfolioLite } from "./types";

export function PortfolioPicker({ portfolios, value, onChange, className }: { portfolios: PortfolioLite[]; value: number | null; onChange: (id: number) => void; className?: string }) {
  return (
    <Select aria-label="Portfolio" className={className} value={value ?? ""} onChange={(e) => onChange(Number(e.target.value))}>
      {portfolios.map((p) => (
        <option key={p.id} value={p.id}>
          {p.name}
        </option>
      ))}
    </Select>
  );
}
