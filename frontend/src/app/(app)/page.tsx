"use client";

import * as React from "react";
import { toast } from "sonner";
import { ArrowDown, ArrowUp, Settings2 } from "lucide-react";
import { useAuth } from "@/components/auth/auth-provider";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { Switch } from "@/components/ui/switch";
import { api, errorMessage } from "@/lib/api";
import { BreadthWidget, IndicesWidget, MoversWidget, SectorsWidget, WatchlistWidget } from "./dashboard-widgets";
import { EXTRA_WIDGETS } from "./dashboard-extra";

interface WidgetDef {
  key: string;
  label: string;
  wide?: boolean;
  render: () => React.ReactNode;
}

const WIDGETS: WidgetDef[] = [
  { key: "indices", label: "Market overview (indices)", wide: true, render: () => <IndicesWidget /> },
  { key: "breadth", label: "Market statistics", render: () => <BreadthWidget /> },
  { key: "movers", label: "Top movers", render: () => <MoversWidget /> },
  { key: "sectors", label: "Sector performance", render: () => <SectorsWidget /> },
  { key: "watchlist", label: "Watchlist", render: () => <WatchlistWidget /> },
  ...EXTRA_WIDGETS,
];

interface Layout {
  order: string[];
  hidden: string[];
}

function resolveLayout(pref: unknown): Layout {
  const p = (pref ?? {}) as Partial<Layout>;
  const known = WIDGETS.map((w) => w.key);
  const order = [...(p.order ?? []).filter((k) => known.includes(k)), ...known.filter((k) => !(p.order ?? []).includes(k))];
  return { order, hidden: (p.hidden ?? []).filter((k) => known.includes(k)) };
}

function CustomizeDialog({ layout, onSave }: { layout: Layout; onSave: (l: Layout) => Promise<void> }) {
  const [open, setOpen] = React.useState(false);
  const [draft, setDraft] = React.useState(layout);
  const move = (i: number, d: -1 | 1) => {
    const order = [...draft.order];
    const j = i + d;
    if (j < 0 || j >= order.length) return;
    [order[i], order[j]] = [order[j], order[i]];
    setDraft({ ...draft, order });
  };
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (o) setDraft(layout);
      }}
    >
      <DialogTrigger asChild>
        <Button size="sm" variant="secondary">
          <Settings2 /> Customize
        </Button>
      </DialogTrigger>
      <DialogContent title="Customize dashboard" description="Choose which widgets to show and their order. Saved to your profile.">
        <ul className="space-y-1">
          {draft.order.map((key, i) => {
            const w = WIDGETS.find((x) => x.key === key)!;
            const visible = !draft.hidden.includes(key);
            return (
              <li key={key} className="flex items-center justify-between rounded border border-border px-3 py-2">
                <span className="text-sm">{w.label}</span>
                <span className="flex items-center gap-1">
                  <Button size="icon" variant="ghost" onClick={() => move(i, -1)} aria-label={`Move ${w.label} up`}>
                    <ArrowUp />
                  </Button>
                  <Button size="icon" variant="ghost" onClick={() => move(i, 1)} aria-label={`Move ${w.label} down`}>
                    <ArrowDown />
                  </Button>
                  <Switch checked={visible} onCheckedChange={(c) => setDraft({ ...draft, hidden: c ? draft.hidden.filter((h) => h !== key) : [...draft.hidden, key] })} aria-label={`Show ${w.label}`} />
                </span>
              </li>
            );
          })}
        </ul>
        <div className="mt-4 flex justify-end">
          <Button
            onClick={async () => {
              await onSave(draft);
              setOpen(false);
            }}
          >
            Save layout
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export default function OverviewPage() {
  const { user, setUser } = useAuth();
  const layout = resolveLayout(user?.preferences?.dashboard);

  async function save(l: Layout) {
    try {
      const updated = await api.patch<NonNullable<typeof user>>("/auth/me/preferences", { preferences: { dashboard: l } });
      setUser(updated);
      toast.success("Dashboard layout saved");
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }

  return (
    <>
      <PageHeader title="Overview" description="Indian equity markets at a glance." actions={<CustomizeDialog layout={layout} onSave={save} />} />
      <div className="grid gap-4 lg:grid-cols-2">
        {layout.order
          .filter((k) => !layout.hidden.includes(k))
          .map((k) => {
            const w = WIDGETS.find((x) => x.key === k)!;
            return (
              <section key={k} className={w.wide ? "lg:col-span-2" : undefined} aria-label={w.label}>
                {w.render()}
              </section>
            );
          })}
      </div>
    </>
  );
}
