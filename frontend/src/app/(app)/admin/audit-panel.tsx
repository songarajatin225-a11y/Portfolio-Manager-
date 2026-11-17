"use client";

import * as React from "react";
import useSWR from "swr";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ErrorNotice, Skeleton } from "@/components/ui/feedback";
import { Input } from "@/components/ui/input";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { fetcher } from "@/lib/api";
import { fmtDateTime } from "@/lib/utils";

interface AuditItem {
  id: number;
  created_at: string;
  user_email: string | null;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  ip: string | null;
  details: Record<string, unknown>;
}

const PAGE = 50;

export function AuditPanel() {
  const [action, setAction] = React.useState("");
  const [offset, setOffset] = React.useState(0);
  const query = `/admin/audit-logs?limit=${PAGE}&offset=${offset}${action ? `&action=${encodeURIComponent(action)}` : ""}`;
  const { data, error, isLoading } = useSWR<{ total: number; items: AuditItem[] }>(query, fetcher);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Audit log</CardTitle>
        <Input
          className="h-8 w-56"
          placeholder="Filter by action prefix (e.g. auth.)"
          value={action}
          onChange={(e) => {
            setAction(e.target.value);
            setOffset(0);
          }}
        />
      </CardHeader>
      <CardContent className="p-0">
        <ErrorNotice error={error} className="m-4" />
        {isLoading ? <Skeleton className="m-4 h-24" /> : null}
        <Table>
          <THead>
            <tr>
              <TH>Time</TH>
              <TH>User</TH>
              <TH>Action</TH>
              <TH>Entity</TH>
              <TH>IP</TH>
              <TH>Details</TH>
            </tr>
          </THead>
          <TBody>
            {data?.items.map((i) => (
              <TR key={i.id}>
                <TD className="whitespace-nowrap text-xs">{fmtDateTime(i.created_at)}</TD>
                <TD className="text-xs">{i.user_email ?? "—"}</TD>
                <TD className="font-mono text-xs">{i.action}</TD>
                <TD className="text-xs text-muted">{i.entity_type ? `${i.entity_type}:${i.entity_id ?? ""}` : "—"}</TD>
                <TD className="text-xs text-muted">{i.ip ?? "—"}</TD>
                <TD className="max-w-xs truncate font-mono text-[11px] text-muted" title={JSON.stringify(i.details)}>
                  {Object.keys(i.details).length ? JSON.stringify(i.details) : ""}
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
        {data ? (
          <div className="flex items-center justify-between border-t border-border px-4 py-2 text-xs text-muted">
            <span>
              {data.total === 0 ? "No entries" : `${offset + 1}–${Math.min(offset + PAGE, data.total)} of ${data.total}`}
            </span>
            <div className="flex gap-2">
              <Button variant="outline" size="xs" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
                Previous
              </Button>
              <Button variant="outline" size="xs" disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)}>
                Next
              </Button>
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
