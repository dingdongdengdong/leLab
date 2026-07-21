import { useCallback, useEffect, useState, type ReactNode } from "react";
import { ArrowLeft, CheckCircle2, ClipboardCheck, Copy, Cpu, RefreshCw, ShieldAlert, Usb } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { useApi } from "@/contexts/ApiContext";

type HardwareReadiness = {
  website_controls_physical_arm: boolean;
  config_template: string;
  arm: { protocol: string; motor_type: string; python_can_available: boolean; requires: string[] };
  hand: { protocol: string; rustypot_available: boolean; serial_ports: string[]; requires: string[] };
  steps: string[];
};

const Status = ({ ready }: { ready: boolean }) => (
  <span className={`rounded-full px-2 py-1 text-xs font-medium ${ready ? "bg-emerald-950 text-emerald-300" : "bg-amber-950 text-amber-300"}`}>
    {ready ? "Detected" : "Install / connect required"}
  </span>
);

export default function HardwareSetup() {
  const navigate = useNavigate();
  const { baseUrl, fetchWithHeaders } = useApi();
  const [readiness, setReadiness] = useState<HardwareReadiness | null>(null);
  const [error, setError] = useState("");
  const [checked, setChecked] = useState<boolean[]>([]);

  const load = useCallback(async () => {
    try {
      const response = await fetchWithHeaders(`${baseUrl}/api/superarm/hardware-readiness`);
      if (!response.ok) throw new Error(`Readiness request failed (${response.status})`);
      const data = (await response.json()) as HardwareReadiness;
      setReadiness(data);
      setChecked((current) => data.steps.map((_, index) => current[index] || false));
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load hardware readiness.");
    }
  }, [baseUrl, fetchWithHeaders]);

  useEffect(() => { void load(); }, [load]);

  const copyTemplate = async () => {
    if (readiness && navigator.clipboard?.writeText) await navigator.clipboard.writeText(readiness.config_template);
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800 bg-slate-950/95 px-4 py-3">
        <div className="mx-auto flex max-w-6xl items-center gap-3">
          <Button variant="ghost" size="icon" onClick={() => navigate("/")} aria-label="Back"><ArrowLeft /></Button>
          <div><h1 className="text-xl font-semibold">Real hardware setup</h1><p className="text-xs text-cyan-400">DM4340P CAN arm + AmazingHand SCS0009 serial hand</p></div>
          <Button className="ml-auto" variant="outline" onClick={() => void load()}><RefreshCw className="mr-2 h-4 w-4" />Refresh status</Button>
        </div>
      </header>
      <main className="mx-auto max-w-6xl space-y-5 p-4">
        <section className="rounded-xl border border-amber-700/70 bg-amber-950/30 p-4">
          <div className="flex gap-3"><ShieldAlert className="mt-0.5 text-amber-300" /><div><h2 className="font-semibold text-amber-200">Preparation page — no motor command is available here</h2><p className="mt-1 text-sm text-amber-100/80">Finish every measured value and isolated bench test before enabling the separate LeRobot hardware adapter. The existing MuJoCo dashboard remains simulation/hybrid-hand only.</p></div></div>
        </section>
        {error && <div className="rounded-lg border border-red-700 bg-red-950/40 p-3 text-sm text-red-200">{error}</div>}
        <section className="grid gap-4 md:grid-cols-2">
          <ProtocolCard icon={<Cpu />} title="Arm: LeRobot Damiao CAN" status={readiness?.arm.python_can_available ?? false} details={readiness?.arm.requires ?? []} footer={`Motor type: ${readiness?.arm.motor_type ?? "loading…"}`} />
          <ProtocolCard icon={<Usb />} title="Hand: AmazingHandControl serial" status={readiness?.hand.rustypot_available ?? false} details={readiness?.hand.requires ?? []} footer={readiness?.hand.serial_ports.length ? `Detected ports: ${readiness.hand.serial_ports.join(", ")}` : "No SCS0009 serial adapter detected"} />
        </section>
        <section className="rounded-xl border border-slate-800 bg-slate-900 p-5">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-2"><div><h2 className="font-semibold">Measured hardware checklist</h2><p className="text-sm text-slate-400">Check items locally as you complete them. This does not enable hardware.</p></div><ClipboardCheck className="text-cyan-400" /></div>
          <ol className="space-y-3">
            {(readiness?.steps ?? ["Loading server checklist…"]).map((step, index) => <li key={step} className="flex items-start gap-3 rounded-lg bg-slate-950 p-3"><input aria-label={`Complete step ${index + 1}`} type="checkbox" checked={checked[index] ?? false} onChange={(event) => setChecked((items) => items.map((value, itemIndex) => itemIndex === index ? event.target.checked : value))} className="mt-1 h-4 w-4 accent-cyan-400" /><span className="text-sm"><strong className="mr-2 text-cyan-300">{index + 1}.</strong>{step}</span></li>)}
          </ol>
        </section>
        <section className="rounded-xl border border-slate-800 bg-slate-900 p-5"><h2 className="font-semibold">Configuration handoff</h2><p className="mt-1 text-sm text-slate-400">Copy the template path, then replace all intentionally invalid placeholders from real discovery and calibration. Do not use OpenArm sample IDs.</p><div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg bg-slate-950 p-3 font-mono text-xs text-slate-300"><code className="min-w-0 flex-1 break-all">{readiness?.config_template ?? "Loading…"}</code><Button size="sm" variant="outline" onClick={() => void copyTemplate()} disabled={!readiness}><Copy className="mr-2 h-3 w-3" />Copy</Button></div><div className="mt-4 flex flex-wrap gap-3"><Button variant="outline" onClick={() => navigate("/calibration")}>Open SuperArm calibration</Button><Button variant="outline" onClick={() => navigate("/so101-leader-setup")}>Open SO-101 leader guide</Button></div></section>
        <p className="text-center text-xs text-slate-500">Protocol status is read-only. A green package status is not a calibration or motion-pass result.</p>
      </main>
    </div>
  );
}

function ProtocolCard({ icon, title, status, details, footer }: { icon: ReactNode; title: string; status: boolean; details: string[]; footer: string }) {
  return <section className="rounded-xl border border-slate-800 bg-slate-900 p-5"><div className="flex items-center gap-3"><span className="rounded-lg bg-cyan-950 p-2 text-cyan-300">{icon}</span><div className="mr-auto"><h2 className="font-semibold">{title}</h2></div><Status ready={status} /></div><ul className="mt-4 space-y-2 text-sm text-slate-300">{details.map((detail) => <li key={detail} className="flex gap-2"><CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-cyan-400" />{detail}</li>)}</ul><p className="mt-4 border-t border-slate-800 pt-3 text-xs text-slate-400">{footer}</p></section>;
}
