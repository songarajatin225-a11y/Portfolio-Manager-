"use client";

import * as React from "react";
import useSWR, { useSWRConfig } from "swr";
import { toast } from "sonner";
import { Plus, RefreshCw, Trash2 } from "lucide-react";
import { useAuth } from "@/components/auth/auth-provider";
import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { EmptyState, ErrorNotice, Skeleton } from "@/components/ui/feedback";
import { Field, Input, Select } from "@/components/ui/input";
import { api, errorMessage, fetcher } from "@/lib/api";
import { timeAgo } from "@/lib/utils";
import { NewsRow, type NewsItem } from "@/components/news/news-row";

interface Source {
  id: number;
  name: string;
  url: string;
  category: string;
  is_active: boolean;
  last_fetched_at: string | null;
  last_error: string | null;
}

function SourcesCard() {
  const { data, mutate } = useSWR<Source[]>("/news/sources", fetcher);
  const { mutate: globalMutate } = useSWRConfig();
  const [open, setOpen] = React.useState(false);
  const [form, setForm] = React.useState({ name: "", url: "", category: "market" });
  const [busy, setBusy] = React.useState(false);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api.post("/news/sources", form);
      setOpen(false);
      setForm({ name: "", url: "", category: "market" });
      void mutate();
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }
  async function ingest() {
    setBusy(true);
    try {
      const r = await api.post<{ added: number; sources: number; errors: { source: string }[] }>("/news/ingest");
      toast.success(`Fetched ${r.sources} feed(s): ${r.added} new article(s)${r.errors.length ? `, ${r.errors.length} failed` : ""}`);
      void mutate();
      // refresh every article list on screen, not just the feed list
      void globalMutate((key) => typeof key === "string" && key.startsWith("/news?"));
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Feeds</CardTitle>
          <CardDescription className="mt-0.5">RSS/Atom feeds from publishers and exchanges. Respect each publisher&apos;s terms of use.</CardDescription>
        </div>
        <div className="flex gap-2">
          <Button size="sm" variant="secondary" onClick={ingest} disabled={busy}>
            <RefreshCw className={busy ? "animate-spin" : ""} /> Fetch now
          </Button>
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
              <Button size="sm">
                <Plus /> Add feed
              </Button>
            </DialogTrigger>
            <DialogContent title="Add news feed">
              <form onSubmit={add} className="space-y-3">
                <Field label="Name">
                  <Input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
                </Field>
                <Field label="Feed URL (RSS or Atom)">
                  <Input required type="url" value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} placeholder="https://" />
                </Field>
                <Field label="Category">
                  <Select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
                    <option value="market">Market news</option>
                    <option value="announcements">Corporate announcements</option>
                    <option value="company">Company news</option>
                    <option value="regulatory">Regulatory</option>
                    <option value="economy">Economy</option>
                  </Select>
                </Field>
                <Button type="submit" className="w-full">
                  Add feed
                </Button>
              </form>
            </DialogContent>
          </Dialog>
        </div>
      </CardHeader>
      <CardContent className="space-y-1">
        {data?.length ? null : <p className="text-xs text-muted">No feeds configured.</p>}
        {data?.map((s) => (
          <div key={s.id} className="flex items-center justify-between gap-2 text-xs">
            <div className="min-w-0">
              <p className="truncate font-medium">
                {s.name} <Badge>{s.category}</Badge>
              </p>
              <p className="truncate text-subtle">{s.url}</p>
              {s.last_error ? <p className="truncate text-down">{s.last_error}</p> : <p className="text-subtle">last fetched {timeAgo(s.last_fetched_at)}</p>}
            </div>
            <Button
              size="icon"
              variant="ghost"
              aria-label={`Remove ${s.name}`}
              onClick={async () => {
                await api.del(`/news/sources/${s.id}`);
                void mutate();
              }}
            >
              <Trash2 />
            </Button>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

export default function NewsPage() {
  const { isAdmin } = useAuth();
  const [filters, setFilters] = React.useState({ sentiment: "", impact: "", category: "", q: "", symbol: "", days: "7" });
  const qs = new URLSearchParams(Object.entries(filters).filter(([, v]) => v)).toString();
  const { data, error, isLoading } = useSWR<{ total: number; sentiment_counts: Record<string, number>; items: NewsItem[] }>(`/news?limit=100&${qs}`, fetcher, { refreshInterval: 60_000 });
  const set = (k: keyof typeof filters) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setFilters({ ...filters, [k]: e.target.value });

  return (
    <>
      <PageHeader title="News Intelligence" description="Aggregated, de-duplicated headlines with company tagging and sentiment. Sentiment is a text classification, not a price signal." />
      <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
        <Card>
          <CardHeader className="flex-wrap">
            <div className="flex flex-wrap gap-2">
              <Input className="h-8 w-48" placeholder="Search headlines" value={filters.q} onChange={set("q")} />
              <Input className="h-8 w-28" placeholder="Symbol" value={filters.symbol} onChange={set("symbol")} />
              <Select className="h-8 w-32" value={filters.sentiment} onChange={set("sentiment")} aria-label="Sentiment">
                <option value="">Any sentiment</option>
                <option value="positive">Positive</option>
                <option value="negative">Negative</option>
                <option value="neutral">Neutral</option>
              </Select>
              <Select className="h-8 w-32" value={filters.impact} onChange={set("impact")} aria-label="Impact">
                <option value="">Any impact</option>
                <option value="high">High impact</option>
              </Select>
              <Select className="h-8 w-40" value={filters.category} onChange={set("category")} aria-label="Category">
                <option value="">All categories</option>
                <option value="market">Market</option>
                <option value="announcements">Announcements</option>
                <option value="company">Company</option>
                <option value="regulatory">Regulatory</option>
                <option value="economy">Economy</option>
              </Select>
              <Select className="h-8 w-28" value={filters.days} onChange={set("days")} aria-label="Period">
                <option value="1">24 hours</option>
                <option value="7">7 days</option>
                <option value="30">30 days</option>
              </Select>
            </div>
            {data ? (
              <div className="flex gap-1 text-xs">
                <Badge variant="up">{data.sentiment_counts.positive ?? 0} positive</Badge>
                <Badge variant="down">{data.sentiment_counts.negative ?? 0} negative</Badge>
                <Badge>{data.sentiment_counts.neutral ?? 0} neutral</Badge>
              </div>
            ) : null}
          </CardHeader>
          <CardContent className="py-0">
            <ErrorNotice error={error} className="my-4" />
            {isLoading ? <Skeleton className="my-4 h-40" /> : null}
            {data && data.items.length === 0 ? <EmptyState title="No articles" description={isAdmin ? "Add RSS feeds on the right, then fetch." : "No news matches these filters."} /> : null}
            <ul className="divide-y divide-border/60">
              {data?.items.map((n) => (
                <NewsRow key={n.id} n={n} />
              ))}
            </ul>
          </CardContent>
        </Card>
        {isAdmin ? <SourcesCard /> : null}
      </div>
    </>
  );
}
