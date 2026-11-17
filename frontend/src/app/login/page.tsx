"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { LineChart } from "lucide-react";
import { useAuth } from "@/components/auth/auth-provider";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ErrorNotice, Spinner } from "@/components/ui/feedback";
import { Field, Input } from "@/components/ui/input";
import { api, errorMessage } from "@/lib/api";

function safeNext(next: string | null): string {
  // Only allow same-site relative paths to avoid open redirects.
  return next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
}

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const { login, register, user } = useAuth();
  const [mode, setMode] = React.useState<"loading" | "login" | "setup" | "register">("loading");
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [fullName, setFullName] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [registrationOpen, setRegistrationOpen] = React.useState(false);

  React.useEffect(() => {
    if (user) router.replace(safeNext(params.get("next")));
  }, [user, router, params]);

  React.useEffect(() => {
    api
      .get<{ needs_setup: boolean; registration_open: boolean }>("/auth/bootstrap-status")
      .then((s) => {
        setRegistrationOpen(s.registration_open);
        setMode(s.needs_setup ? "setup" : "login");
      })
      .catch((e) => {
        setError(errorMessage(e));
        setMode("login");
      });
  }, []);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "login") await login(email, password);
      else await register(email, password, fullName);
      // Next.js keeps recently visited routes mounted (hidden) - don't leave the password in memory.
      setPassword("");
      router.replace(safeNext(params.get("next")));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (mode === "loading") {
    return (
      <div className="flex justify-center py-10">
        <Spinner />
      </div>
    );
  }

  const isSignup = mode === "setup" || mode === "register";
  return (
    <form onSubmit={onSubmit} className="space-y-4">
      {mode === "setup" ? (
        <p className="rounded-md border border-info/30 bg-info/10 p-3 text-xs text-info">
          No accounts exist yet. The account you create now becomes the administrator.
        </p>
      ) : null}
      {isSignup ? (
        <Field label="Full name">
          <Input value={fullName} onChange={(e) => setFullName(e.target.value)} autoComplete="name" />
        </Field>
      ) : null}
      <Field label="Email">
        <Input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
      </Field>
      <Field label="Password" hint={isSignup ? "At least 10 characters, with upper and lower case letters and a digit." : undefined}>
        <Input
          type="password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete={isSignup ? "new-password" : "current-password"}
        />
      </Field>
      <ErrorNotice error={error} />
      <Button type="submit" className="w-full" disabled={busy}>
        {busy ? <Spinner className="text-white" /> : null}
        {mode === "setup" ? "Create administrator account" : isSignup ? "Create account" : "Sign in"}
      </Button>
      {mode === "login" && registrationOpen ? (
        <button type="button" className="w-full text-xs text-muted hover:text-foreground" onClick={() => setMode("register")}>
          Need an account? Register
        </button>
      ) : null}
      {mode === "register" ? (
        <button type="button" className="w-full text-xs text-muted hover:text-foreground" onClick={() => setMode("login")}>
          Already have an account? Sign in
        </button>
      ) : null}
    </form>
  );
}

export default function LoginPage() {
  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center justify-center gap-2">
          <LineChart className="size-6 text-accent" />
          <span className="text-lg font-semibold tracking-wide">MarketIntel</span>
        </div>
        <Card>
          <CardContent className="p-6">
            <React.Suspense
              fallback={
                <div className="flex justify-center py-10">
                  <Spinner />
                </div>
              }
            >
              <LoginForm />
            </React.Suspense>
          </CardContent>
        </Card>
        <p className="mt-4 text-center text-[11px] text-subtle">
          Market data, analytics and AI output are for information only and are not investment advice.
        </p>
      </div>
    </main>
  );
}
