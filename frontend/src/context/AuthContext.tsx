import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { apiClient, AUTH_LOGOUT_EVENT } from "@/api/client";
import { queryClient } from "@/lib/queryClient";
import { resetCompanyCache } from "@/api/lookups";
import type { UserProfile } from "@/types";

export type { UserProfile };

interface AuthContextValue {
  user: UserProfile | null;
  loading: boolean;
  isAuthenticated: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  refetchUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState<boolean>(true);

  const fetchUser = useCallback(async () => {
    const token = localStorage.getItem("access_token");
    if (!token) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      const response = await apiClient.get<UserProfile>("/auth/me/");
      setUser(response.data);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const clear = useCallback(() => {
    setUser(null);
    queryClient.clear();
    resetCompanyCache();
  }, []);

  useEffect(() => {
    fetchUser();
  }, [fetchUser]);

  // The API client fires this when a 401 cannot be recovered by a token refresh.
  useEffect(() => {
    window.addEventListener(AUTH_LOGOUT_EVENT, clear);
    return () => window.removeEventListener(AUTH_LOGOUT_EVENT, clear);
  }, [clear]);

  const login = useCallback(
    async (username: string, password: string) => {
      const response = await apiClient.post("/auth/token/", { username, password });
      localStorage.setItem("access_token", response.data.access);
      localStorage.setItem("refresh_token", response.data.refresh);
      queryClient.clear();
      resetCompanyCache();
      await fetchUser();
    },
    [fetchUser]
  );

  const logout = useCallback(() => {
    localStorage.removeItem("access_token");
    localStorage.removeItem("refresh_token");
    clear(); // ProtectedRoute then redirects to /login
  }, [clear]);

  const value = useMemo<AuthContextValue>(
    () => ({ user, loading, isAuthenticated: !!user, login, logout, refetchUser: fetchUser }),
    [user, loading, login, logout, fetchUser]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
