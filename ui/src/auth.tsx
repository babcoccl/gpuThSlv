import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, isAuthError } from "./api/client";

interface AuthState {
  /** null until the first probe finishes. */
  authed: boolean | null;
  expiresAt: number | null;
  login: (key: string) => Promise<void>;
  logout: () => Promise<void>;
  expire: () => void;
}

const AuthCtx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [expiresAt, setExpiresAt] = useState<number | null>(null);

  useEffect(() => {
    // Probe with a cheap authenticated endpoint; 401 -> show login.
    api
      .listGpus()
      .then(() => setAuthed(true))
      .catch((err) => setAuthed(!isAuthError(err)));
  }, []);

  const login = useCallback(async (key: string) => {
    const resp = await api.login(key);
    setExpiresAt(resp.expires_at);
    setAuthed(true);
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      setAuthed(false);
      setExpiresAt(null);
    }
  }, []);

  const expire = useCallback(() => setAuthed(false), []);

  return (
    <AuthCtx.Provider value={{ authed, expiresAt, login, logout, expire }}>
      {children}
    </AuthCtx.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
