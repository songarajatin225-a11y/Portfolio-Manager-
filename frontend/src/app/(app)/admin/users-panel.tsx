"use client";

import * as React from "react";
import useSWR from "swr";
import { toast } from "sonner";
import { Plus } from "lucide-react";
import { useAuth, type User } from "@/components/auth/auth-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { ErrorNotice, Skeleton } from "@/components/ui/feedback";
import { Field, Input, Select } from "@/components/ui/input";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, errorMessage, fetcher } from "@/lib/api";
import { fmtDateTime } from "@/lib/utils";

function CreateUserDialog({ onCreated }: { onCreated: () => void }) {
  const [open, setOpen] = React.useState(false);
  const [form, setForm] = React.useState({ email: "", full_name: "", password: "", role: "analyst" });
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/users", form);
      toast.success(`Created ${form.email}`);
      setOpen(false);
      setForm({ email: "", full_name: "", password: "", role: "analyst" });
      onCreated();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm">
          <Plus /> Add user
        </Button>
      </DialogTrigger>
      <DialogContent title="Add user" description="Admins manage settings and users. Analysts can create portfolios, alerts and research. Viewers are read-only.">
        <form onSubmit={submit} className="space-y-3">
          <Field label="Email">
            <Input type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
          </Field>
          <Field label="Full name">
            <Input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} />
          </Field>
          <Field label="Initial password" hint="At least 10 characters with upper/lower case and a digit.">
            <Input type="password" required value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
          </Field>
          <Field label="Role">
            <Select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
              <option value="analyst">Analyst</option>
              <option value="viewer">Viewer</option>
              <option value="admin">Admin</option>
            </Select>
          </Field>
          <ErrorNotice error={error} />
          <Button type="submit" disabled={busy} className="w-full">
            Create user
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function UsersPanel() {
  const { user: me } = useAuth();
  const { data, error, isLoading, mutate } = useSWR<User[]>("/users", fetcher);

  async function update(id: string, patch: Record<string, unknown>) {
    try {
      await api.patch(`/users/${id}`, patch);
      toast.success("User updated");
      void mutate();
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Users</CardTitle>
        <CreateUserDialog onCreated={() => mutate()} />
      </CardHeader>
      <CardContent className="p-0">
        <ErrorNotice error={error} className="m-4" />
        {isLoading ? <Skeleton className="m-4 h-24" /> : null}
        <Table>
          <THead>
            <tr>
              <TH>User</TH>
              <TH>Role</TH>
              <TH>Status</TH>
              <TH>Last login</TH>
              <TH className="text-right">Actions</TH>
            </tr>
          </THead>
          <TBody>
            {data?.map((u) => (
              <TR key={u.id}>
                <TD>
                  <p className="font-medium">{u.full_name || "—"}</p>
                  <p className="text-xs text-muted">{u.email}</p>
                </TD>
                <TD>
                  <Select
                    className="h-8 w-28"
                    value={u.role}
                    disabled={u.id === me?.id}
                    onChange={(e) => update(u.id, { role: e.target.value })}
                    aria-label={`Role for ${u.email}`}
                  >
                    <option value="admin">Admin</option>
                    <option value="analyst">Analyst</option>
                    <option value="viewer">Viewer</option>
                  </Select>
                </TD>
                <TD>
                  <Badge variant={u.is_active ? "up" : "down"}>{u.is_active ? "active" : "disabled"}</Badge>
                </TD>
                <TD className="text-xs text-muted">{fmtDateTime(u.last_login_at)}</TD>
                <TD className="text-right">
                  {u.id !== me?.id ? (
                    <Button variant="outline" size="xs" onClick={() => update(u.id, { is_active: !u.is_active })}>
                      {u.is_active ? "Disable" : "Enable"}
                    </Button>
                  ) : (
                    <span className="text-xs text-subtle">you</span>
                  )}
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      </CardContent>
    </Card>
  );
}
