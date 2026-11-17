"use client";

import * as React from "react";
import useSWR from "swr";
import { api, ApiError } from "@/lib/api";

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: "admin" | "analyst" | "viewer";
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
  preferences: Record<string, unknown>;
}

interface SessionResponse {
  user: User;
  csrf_token: string;
}

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<User>;
  register: (email: string, password: string, fullName: string) => Promise<User>;
  logout: () => Promise<void>;
  reload: () => Promise<void>;
  setUser: (u: User | null) => void;
  canWrite: boolean;
  isAdmin: boolean;
}

const AuthContext = React.createContext<AuthContextValue | null>(null);

async function loadSession(): Promise<User | null> {
  try {
    return await api.get<User>("/auth/me");
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return null;
    throw err;
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const { data, isLoading, mutate } = useSWR<User | null>("session", loadSession, {
    revalidateOnFocus: true,
    shouldRetryOnError: false,
  });
  const user = data ?? null;
  const loading = isLoading;

  const setUser = React.useCallback((u: User | null) => void mutate(u, { revalidate: false }), [mutate]);
  const reload = React.useCallback(async () => {
    await mutate();
  }, [mutate]);

  const login = React.useCallback(
    async (email: string, password: string) => {
      const res = await api.post<SessionResponse>("/auth/login", { email, password });
      setUser(res.user);
      return res.user;
    },
    [setUser],
  );

  const register = React.useCallback(
    async (email: string, password: string, fullName: string) => {
      const res = await api.post<SessionResponse>("/auth/register", { email, password, full_name: fullName });
      setUser(res.user);
      return res.user;
    },
    [setUser],
  );

  const logout = React.useCallback(async () => {
    try {
      await api.post("/auth/logout");
    } finally {
      setUser(null);
    }
  }, [setUser]);

  const value = React.useMemo<AuthContextValue>(
    () => ({
      user,
      loading,
      login,
      register,
      logout,
      reload,
      setUser,
      canWrite: user?.role === "admin" || user?.role === "analyst",
      isAdmin: user?.role === "admin",
    }),
    [user, loading, login, register, logout, reload, setUser],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = React.useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
