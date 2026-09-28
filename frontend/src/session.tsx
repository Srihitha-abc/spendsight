import { createContext, ReactNode, useContext, useEffect, useState } from "react";
import { setWorkspaceId, Workspace } from "./api";

/** Mock auth + workspace selection. No real authentication: the user is kept in React context and localStorage. */
export interface User { name: string; email: string; provider: "password" | "google" }

export type Route = "login" | "workspaces" | "data" | "mapping" | "processing" | "dashboard" | "replay" | "compare" | "insights";
const APP_ROUTES: Route[] = ["dashboard", "replay", "compare", "insights"];
export const isAppRoute = (r: Route) => APP_ROUTES.includes(r);

interface Session {
  user: User | null; workspace: Workspace | null; route: Route;
  login: (u: User) => void; logout: () => void;
  selectWorkspace: (w: Workspace | null) => void; go: (r: Route) => void;
}

const Ctx = createContext<Session | null>(null);

const load = <T,>(k: string): T | null => {
  try { const v = localStorage.getItem(k); return v ? (JSON.parse(v) as T) : null; } catch { return null; }
};
const save = (k: string, v: unknown) => {
  try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ }
};
const routeFromHash = (): Route => {
  const r = window.location.hash.replace(/^#\/?/, "") as Route;
  return (["login", "workspaces", "data", "mapping", "processing", ...APP_ROUTES] as Route[]).includes(r) ? r : "dashboard";
};

export function nameFromEmail(email: string): string {
  const local = email.split("@")[0] || "user";
  const pretty = local.replace(/[._-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  return pretty.replace(/\bDevops\b/g, "DevOps").replace(/\bSre\b/g, "SRE").replace(/\bFinops\b/g, "FinOps");
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(() => load<User>("ss.user"));
  const [workspace, setWs] = useState<Workspace | null>(() => load<Workspace>("ss.workspace"));
  const [route, setRoute] = useState<Route>(routeFromHash);

  if (workspace) setWorkspaceId(workspace.id);

  useEffect(() => {
    const onHash = () => setRoute(routeFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // Guards: no user -> login; no workspace -> picker.
  const effective: Route = !user ? "login" : !workspace && route !== "login" ? "workspaces" : route === "login" ? "workspaces" : route;

  const go = (r: Route) => { window.location.hash = `/${r}`; setRoute(r); };
  const value: Session = {
    user, workspace, route: effective, go,
    login: (u) => { setUser(u); save("ss.user", u); go("workspaces"); },
    logout: () => { setUser(null); setWs(null); save("ss.user", null); save("ss.workspace", null); go("login"); },
    selectWorkspace: (w) => { setWs(w); save("ss.workspace", w); if (w) setWorkspaceId(w.id); },
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession(): Session {
  const s = useContext(Ctx);
  if (!s) throw new Error("useSession outside SessionProvider");
  return s;
}
