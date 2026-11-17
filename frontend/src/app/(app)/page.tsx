"use client";

import Link from "next/link";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { SystemStatusPanel } from "./admin/status-panel";

export default function OverviewPage() {
  return (
    <>
      <PageHeader
        title="Overview"
        description="Connect a market data provider to start streaming prices."
        actions={
          <Button asChild size="sm" variant="secondary">
            <Link href="/admin?tab=settings">Platform settings</Link>
          </Button>
        }
      />
      <SystemStatusPanel />
    </>
  );
}
