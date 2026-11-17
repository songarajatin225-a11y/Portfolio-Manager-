"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Spinner } from "@/components/ui/feedback";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PageHeader } from "@/components/layout/page-header";
import { useAuth } from "@/components/auth/auth-provider";
import { SystemStatusPanel } from "./status-panel";
import { UsersPanel } from "./users-panel";
import { SettingsPanel } from "./settings-panel";
import { AuditPanel } from "./audit-panel";
import { ImportsPanel } from "./imports-panel";

function AdminTabs() {
  const { isAdmin } = useAuth();
  const params = useSearchParams();
  const router = useRouter();
  const tab = params.get("tab") ?? "status";
  return (
    <Tabs value={isAdmin ? tab : "status"} onValueChange={(v) => router.replace(`/admin?tab=${v}`)}>
      <TabsList>
        <TabsTrigger value="status">System Status</TabsTrigger>
        {isAdmin ? (
          <>
            <TabsTrigger value="settings">Settings</TabsTrigger>
            <TabsTrigger value="imports">Data Imports</TabsTrigger>
            <TabsTrigger value="users">Users</TabsTrigger>
            <TabsTrigger value="audit">Audit Log</TabsTrigger>
          </>
        ) : null}
      </TabsList>
      <TabsContent value="status">
        <SystemStatusPanel />
      </TabsContent>
      {isAdmin ? (
        <>
          <TabsContent value="settings">
            <SettingsPanel />
          </TabsContent>
          <TabsContent value="imports">
            <ImportsPanel />
          </TabsContent>
          <TabsContent value="users">
            <UsersPanel />
          </TabsContent>
          <TabsContent value="audit">
            <AuditPanel />
          </TabsContent>
        </>
      ) : null}
    </Tabs>
  );
}

export default function AdminPage() {
  return (
    <>
      <PageHeader title="System Administration" description="Service health, platform settings, users and the audit trail." />
      <React.Suspense fallback={<Spinner />}>
        <AdminTabs />
      </React.Suspense>
    </>
  );
}
