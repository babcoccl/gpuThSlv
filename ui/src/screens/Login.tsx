import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAuth } from "../auth";

export default function Login() {
  const { login } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const [key, setKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(key);
      nav((loc.state as { from?: string } | null)?.from ?? "/jobs", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="login-box panel">
      <h1>Sign in</h1>
      <form onSubmit={submit}>
        <label htmlFor="api-key">API key</label>
        <input
          id="api-key"
          type="password"
          autoComplete="current-password"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          autoFocus
        />
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" disabled={busy || !key}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
      <p className="kbd-hint" style={{ marginTop: 10 }}>
        Exchanges the key for a session cookie; nothing is stored in the browser.
      </p>
    </main>
  );
}
