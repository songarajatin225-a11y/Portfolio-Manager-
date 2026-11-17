"use client";

import * as React from "react";
import useSWR from "swr";
import { Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import { fetcher } from "@/lib/api";
import type { Instrument } from "@/lib/types";
import { cn } from "@/lib/utils";

function useDebounced<T>(value: T, ms = 250): T {
  const [v, setV] = React.useState(value);
  React.useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** Typeahead over the instrument master (current data mode only). */
export function InstrumentSearch({ onSelect, placeholder = "Search symbol or company…", segment, className, autoFocus }: { onSelect: (i: Instrument) => void; placeholder?: string; segment?: string; className?: string; autoFocus?: boolean }) {
  const [q, setQ] = React.useState("");
  const [open, setOpen] = React.useState(false);
  const [active, setActive] = React.useState(0);
  const dq = useDebounced(q.trim());
  const { data } = useSWR<Instrument[]>(dq ? `/instruments/search?q=${encodeURIComponent(dq)}&limit=12${segment ? `&segment=${segment}` : ""}` : null, fetcher);
  const results = data ?? [];

  function choose(i: Instrument) {
    onSelect(i);
    setQ("");
    setOpen(false);
  }

  return (
    <div className={cn("relative", className)}>
      <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-subtle" />
      <Input
        value={q}
        autoFocus={autoFocus}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
          setActive(0);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") setActive((a) => Math.min(a + 1, results.length - 1));
          else if (e.key === "ArrowUp") setActive((a) => Math.max(a - 1, 0));
          else if (e.key === "Enter" && results[active]) {
            e.preventDefault();
            choose(results[active]);
          } else if (e.key === "Escape") setOpen(false);
        }}
        placeholder={placeholder}
        className="pl-8"
        role="combobox"
        aria-expanded={open && results.length > 0}
        aria-label={placeholder}
      />
      {open && dq && (
        <ul role="listbox" className="absolute z-40 mt-1 max-h-80 w-full overflow-y-auto rounded-md border border-border-strong bg-surface-2 py-1 shadow-xl">
          {results.length === 0 ? <li className="px-3 py-2 text-xs text-muted">No matches. Sync the instrument master if this is unexpected.</li> : null}
          {results.map((i, idx) => (
            <li
              key={i.id}
              role="option"
              aria-selected={idx === active}
              onMouseDown={(e) => {
                e.preventDefault();
                choose(i);
              }}
              onMouseEnter={() => setActive(idx)}
              className={cn("flex cursor-pointer items-center justify-between gap-3 px-3 py-1.5 text-sm", idx === active && "bg-surface-3")}
            >
              <span className="min-w-0">
                <span className="font-medium">{i.symbol}</span>
                <span className="ml-2 truncate text-xs text-muted">{i.name}</span>
              </span>
              <span className="shrink-0 text-[10px] uppercase text-subtle">
                {i.exchange} · {i.segment}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
