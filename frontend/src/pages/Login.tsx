import { FormEvent, useState } from "react";
import { Logo } from "../components/Brand";
import { nameFromEmail, useSession } from "../session";

export function Login() {
  const { login } = useSession();
  const [email, setEmail] = useState("devops@acmelogistics.com");
  const [password, setPassword] = useState("spendsight-demo");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!email.includes("@")) return;
    login({ email, name: nameFromEmail(email), provider: "password" });
  };

  return (
    <div className="min-h-screen grid lg:grid-cols-2">
      <section className="hidden lg:flex flex-col justify-between p-10" style={{ background: "var(--surface-2)" }}>
        <div className="flex items-center gap-2"><Logo /><span className="font-semibold">SpendSight</span></div>
        <div className="max-w-md">
          <h1 className="text-3xl font-semibold leading-tight">Your cloud bill spiked. Again.<br />This time, the agent remembers why.</h1>
          <ul className="mt-6 flex flex-col gap-3 text-sm secondary">
            <li>📈 Statistical anomaly detection on daily spend, per account, service and team</li>
            <li>🧠 Hindsight memory of past spikes, engineer feedback and deploys</li>
            <li>✓ Expected spikes stop paging you once someone explains them</li>
            <li>✕ New waste (forgotten GPUs, weekend staging, egress bugs) still gets caught</li>
          </ul>
        </div>
        <p className="text-xs muted">Works with AWS CUR, Cost Explorer, GCP, Azure and FOCUS exports.</p>
      </section>

      <section className="flex items-center justify-center p-6">
        <form onSubmit={submit} className="card p-6 w-full max-w-sm flex flex-col gap-4">
          <div className="flex items-center gap-2 lg:hidden"><Logo /><span className="font-semibold">SpendSight</span></div>
          <div>
            <h2 className="text-lg font-semibold">Sign in</h2>
            <p className="text-xs muted">Demo login: no real authentication.</p>
          </div>
          <label className="flex flex-col gap-1 text-xs font-medium">
            Work email
            <input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium">
            Password
            <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
          </label>
          <button className="btn btn-primary" type="submit" style={{ padding: "8px 12px" }}>Sign in</button>
          <div className="flex items-center gap-2 text-[11px] muted">
            <span className="flex-1 h-px" style={{ background: "var(--border)" }} />or<span className="flex-1 h-px" style={{ background: "var(--border)" }} />
          </div>
          <button type="button" className="btn flex items-center justify-center gap-2" style={{ padding: "8px 12px" }}
                  onClick={() => login({ email, name: nameFromEmail(email), provider: "google" })}>
            <span className="w-4 h-4 rounded-full grid place-items-center text-[10px] font-bold" style={{ border: "1px solid var(--axis)" }}>G</span>
            Continue with Google
          </button>
        </form>
      </section>
    </div>
  );
}
