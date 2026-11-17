"use client";

import * as React from "react";
import useSWR from "swr";
import { toast } from "sonner";
import { Bot, FileText, MessageSquarePlus, Send, Trash2, Wrench } from "lucide-react";
import { useAuth } from "@/components/auth/auth-provider";
import { Markdown, ReportView, SourcesList, type AISource, type ResearchReport } from "@/components/ai/ai-parts";
import { PageHeader } from "@/components/layout/page-header";
import { InstrumentSearch } from "@/components/market/instrument-search";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorNotice, Notice, Spinner } from "@/components/ui/feedback";
import { Textarea } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, errorMessage, fetcher } from "@/lib/api";
import { cn, fmtDateTime } from "@/lib/utils";

interface Status {
  configured: boolean;
  enabled: boolean;
  model: string;
}
interface ChatMessage {
  id?: number;
  role: "user" | "assistant";
  content: string;
  sources?: AISource[];
  tool_calls?: { tool: string; input: Record<string, unknown>; is_error: boolean }[];
}
interface Conversation {
  id: number;
  title: string;
  updated_at: string;
}

const SUGGESTIONS = [
  "What is happening in the Indian stock market today?",
  "Which sectors are showing relative strength?",
  "Which stocks are gaining momentum?",
  "Which stocks have unusual volume?",
  "Which companies have improving fundamentals?",
  "Which stocks are showing bearish technical patterns?",
  "Summarise the important market news.",
];

function MessageBubble({ m }: { m: ChatMessage }) {
  if (m.role === "user") {
    return <div className="ml-auto max-w-[85%] rounded-lg bg-accent/15 px-3 py-2 text-sm">{m.content}</div>;
  }
  return (
    <div className="max-w-full space-y-2 rounded-lg border border-border bg-surface-2 px-3 py-2.5">
      <Markdown>{m.content}</Markdown>
      {m.tool_calls?.length ? (
        <div className="flex flex-wrap gap-1">
          {m.tool_calls.map((t, i) => (
            <Badge key={i} variant={t.is_error ? "warn" : "default"} title={JSON.stringify(t.input)}>
              <Wrench className="size-3" /> {t.tool}
            </Badge>
          ))}
        </div>
      ) : null}
      {m.sources ? <SourcesList sources={m.sources} /> : null}
    </div>
  );
}

function ChatPanel({ status }: { status: Status | undefined }) {
  const { canWrite } = useAuth();
  const { data: convs, mutate: mutateConvs } = useSWR<Conversation[]>("/ai/conversations", fetcher);
  const [convId, setConvId] = React.useState<number | null>(null);
  const [messages, setMessages] = React.useState<ChatMessage[]>([]);
  const [input, setInput] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const bottom = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => bottom.current?.scrollIntoView({ behavior: "smooth" }), [messages, busy]);

  async function openConversation(id: number) {
    try {
      const c = await api.get<{ messages: ChatMessage[] }>(`/ai/conversations/${id}`);
      setConvId(id);
      setMessages(c.messages);
      setError(null);
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }

  async function send(text: string) {
    const q = text.trim();
    if (!q || busy) return;
    setMessages((m) => [...m, { role: "user", content: q }]);
    setInput("");
    setBusy(true);
    setError(null);
    try {
      const r = await api.post<{ conversation_id: number; answer: string; sources: AISource[]; tool_calls: ChatMessage["tool_calls"] }>("/ai/chat", {
        message: q,
        conversation_id: convId,
      });
      setConvId(r.conversation_id);
      setMessages((m) => [...m, { role: "assistant", content: r.answer, sources: r.sources, tool_calls: r.tool_calls }]);
      void mutateConvs();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const disabled = !status?.configured || !status.enabled || !canWrite;
  return (
    <div className="grid gap-4 lg:grid-cols-[240px_1fr]">
      <Card className="h-fit">
        <CardHeader>
          <CardTitle>Conversations</CardTitle>
          <Button
            size="icon"
            variant="ghost"
            aria-label="New conversation"
            onClick={() => {
              setConvId(null);
              setMessages([]);
            }}
          >
            <MessageSquarePlus />
          </Button>
        </CardHeader>
        <CardContent className="max-h-[60vh] space-y-0.5 overflow-y-auto p-2">
          {convs?.length ? null : <p className="px-2 text-xs text-subtle">No conversations yet.</p>}
          {convs?.map((c) => (
            <div key={c.id} className={cn("group flex items-center gap-1 rounded px-2 py-1.5 text-xs", c.id === convId ? "bg-surface-3" : "hover:bg-surface-2")}>
              <button type="button" className="min-w-0 flex-1 text-left" onClick={() => openConversation(c.id)}>
                <p className="truncate">{c.title || "Untitled"}</p>
                <p className="text-[10px] text-subtle">{fmtDateTime(c.updated_at)}</p>
              </button>
              {canWrite ? (
                <button
                  type="button"
                  aria-label="Delete conversation"
                  className="hidden rounded p-1 text-muted hover:text-down group-hover:block"
                  onClick={async () => {
                    await api.del(`/ai/conversations/${c.id}`);
                    if (c.id === convId) {
                      setConvId(null);
                      setMessages([]);
                    }
                    void mutateConvs();
                  }}
                >
                  <Trash2 className="size-3.5" />
                </button>
              ) : null}
            </div>
          ))}
        </CardContent>
      </Card>
      <Card className="flex min-h-[70vh] flex-col">
        <CardContent className="flex-1 space-y-3 overflow-y-auto p-4">
          {messages.length === 0 ? (
            <div className="space-y-4 py-6">
              <EmptyState icon={<Bot className="size-8" />} title="Ask about the market, a stock or your watchlists" description="Answers use only data retrieved from this platform, with sources and timestamps. Not investment advice." />
              <div className="flex flex-wrap justify-center gap-2">
                {SUGGESTIONS.map((s) => (
                  <button key={s} type="button" disabled={disabled} onClick={() => send(s)} className="rounded-full border border-border px-3 py-1 text-xs text-muted hover:border-border-strong hover:text-foreground disabled:opacity-50">
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((m, i) => <MessageBubble key={m.id ?? i} m={m} />)
          )}
          {busy ? (
            <div className="flex items-center gap-2 text-xs text-muted">
              <Spinner /> Retrieving data and analysing…
            </div>
          ) : null}
          <ErrorNotice error={error} />
          <div ref={bottom} />
        </CardContent>
        <form
          className="flex gap-2 border-t border-border p-3"
          onSubmit={(e) => {
            e.preventDefault();
            void send(input);
          }}
        >
          <Textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send(input);
              }
            }}
            placeholder={disabled ? "AI is not available" : "Ask a question (Shift+Enter for a new line)"}
            disabled={disabled}
            rows={2}
            className="min-h-0 resize-none"
            aria-label="Message"
          />
          <Button type="submit" disabled={disabled || busy || !input.trim()} aria-label="Send">
            <Send />
          </Button>
        </form>
      </Card>
    </div>
  );
}

function ReportsPanel({ status }: { status: Status | undefined }) {
  const { canWrite } = useAuth();
  const { data: list, mutate } = useSWR<{ id: number; symbol: string; created_at: string; completeness: number | null; model: string }[]>("/ai/reports", fetcher);
  const [selected, setSelected] = React.useState<number | null>(null);
  const { data: report } = useSWR<ResearchReport>(selected ? `/ai/reports/${selected}` : null, fetcher);
  const [busy, setBusy] = React.useState<string | null>(null);

  async function generate(symbol: string) {
    setBusy(symbol);
    try {
      const r = await api.post<ResearchReport>("/ai/reports", { symbol });
      await mutate();
      setSelected(r.id);
      toast.success(`Report for ${symbol} generated`);
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  const disabled = !status?.configured || !status.enabled || !canWrite;
  return (
    <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
      <Card className="h-fit">
        <CardHeader>
          <CardTitle>Reports</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 p-3">
          {!disabled ? <InstrumentSearch placeholder="Generate report for…" onSelect={(i) => generate(i.symbol)} /> : null}
          {busy ? (
            <p className="flex items-center gap-2 text-xs text-muted">
              <Spinner /> Gathering data and writing the {busy} report (can take a minute)…
            </p>
          ) : null}
          <div className="space-y-0.5">
            {list?.length ? null : <p className="text-xs text-subtle">No reports yet.</p>}
            {list?.map((r) => (
              <button key={r.id} type="button" onClick={() => setSelected(r.id)} className={cn("flex w-full items-center justify-between rounded px-2 py-1.5 text-left text-xs", r.id === selected ? "bg-surface-3" : "hover:bg-surface-2")}>
                <span>
                  <span className="font-medium">{r.symbol}</span>
                  <span className="block text-[10px] text-subtle">{fmtDateTime(r.created_at)}</span>
                </span>
                {r.completeness != null ? <Badge>{Math.round(r.completeness * 100)}%</Badge> : null}
              </button>
            ))}
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardContent className="p-4">
          {report ? <ReportView report={report} /> : <EmptyState icon={<FileText className="size-8" />} title="Select or generate a research report" description="Reports cover 12 sections built from the platform's quotes, technicals, fundamentals, news and ML outputs." />}
        </CardContent>
      </Card>
    </div>
  );
}

export default function AIResearchPage() {
  const { data: status } = useSWR<Status>("/ai/status", fetcher);
  return (
    <>
      <PageHeader title="AI Research" description={status ? `Claude (${status.model}) with tools over this platform's data. Research, not investment advice.` : "Loading…"} />
      {status && !status.configured ? <Notice tone="warn" className="mb-4">AI is not configured: set ANTHROPIC_API_KEY on the backend server.</Notice> : null}
      {status && status.configured && !status.enabled ? <Notice tone="warn" className="mb-4">AI features are disabled in System Administration → Settings.</Notice> : null}
      <Tabs defaultValue="chat">
        <TabsList>
          <TabsTrigger value="chat">Research assistant</TabsTrigger>
          <TabsTrigger value="reports">Research reports</TabsTrigger>
        </TabsList>
        <TabsContent value="chat">
          <ChatPanel status={status} />
        </TabsContent>
        <TabsContent value="reports">
          <ReportsPanel status={status} />
        </TabsContent>
      </Tabs>
    </>
  );
}
