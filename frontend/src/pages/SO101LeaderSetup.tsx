import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, Cable, CheckCircle2, Gamepad2, Route, Settings2, ShieldAlert } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { useApi } from "@/contexts/ApiContext";

type LeaderReadiness = {
  supported: boolean;
  manual_page_is_physical_leader: boolean;
  recording_input_mode: string;
  leader: { protocol: string; serial_ports: string[]; requires: string[] };
  follower: {
    device_type: string;
    first_target: string;
    hardware_config_template: string;
    arm_calibration: { required_before_hardware: boolean; joints: string[]; format: string; requires: string[] };
  };
  mapping: { source: string; target: string; sign: number; offset_rad: number }[];
  gripper: { source: string; target: string; motions: { name: string; code: number; degrees: number }[] };
  steps: string[];
};

export default function SO101LeaderSetup() {
  const navigate = useNavigate();
  const { baseUrl, fetchWithHeaders } = useApi();
  const [guide, setGuide] = useState<LeaderReadiness | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const response = await fetchWithHeaders(`${baseUrl}/api/superarm/so101-leader-readiness`);
      if (!response.ok) throw new Error(`Leader guide request failed (${response.status})`);
      setGuide((await response.json()) as LeaderReadiness);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load the SO-101 guide.");
    }
  }, [baseUrl, fetchWithHeaders]);

  useEffect(() => { void load(); }, [load]);

  return <div className="min-h-screen bg-slate-950 text-slate-100"><header className="border-b border-slate-800 px-4 py-3"><div className="mx-auto flex max-w-6xl items-center gap-3"><Button variant="ghost" size="icon" onClick={() => navigate("/")} aria-label="Back"><ArrowLeft /></Button><div><h1 className="text-xl font-semibold">SO-101 Leader → SuperArm</h1><p className="text-xs text-cyan-400">Existing LeRobot SO-101 leader, mapped to five arm controls + one fixed grasp</p></div></div></header><main className="mx-auto max-w-6xl space-y-5 p-4">
    <section className="rounded-xl border border-amber-700/70 bg-amber-950/30 p-4"><div className="flex gap-3"><ShieldAlert className="mt-0.5 text-amber-300" /><div><h2 className="font-semibold text-amber-200">First follower target is MuJoCo</h2><p className="mt-1 text-sm text-amber-100/80">The Manual Web Leader page is sliders only. The physical SO-101 path is used by SuperArm recording. Do the dry-run episode before using the real DM4340P follower.</p></div></div></section>
    {error && <p className="rounded-lg border border-red-700 bg-red-950/40 p-3 text-red-200">{error}</p>}
    <section className="grid gap-4 md:grid-cols-2"><article className="rounded-xl border border-slate-800 bg-slate-900 p-5"><div className="flex items-center gap-3"><Gamepad2 className="text-cyan-300" /><h2 className="font-semibold">Leader connection</h2></div><p className="mt-3 text-sm text-slate-300">{guide?.leader.protocol ?? "Loading…"}</p><ul className="mt-3 space-y-2 text-sm text-slate-400">{(guide?.leader.requires ?? []).map((item) => <li key={item} className="flex gap-2"><CheckCircle2 className="mt-0.5 h-4 w-4 text-cyan-400" />{item}</li>)}</ul><p className="mt-4 text-xs text-slate-500">Detected serial ports: {guide?.leader.serial_ports.join(", ") || "none"}</p><Button className="mt-4" variant="outline" onClick={() => navigate("/calibration")}>Open SO-101 calibration</Button></article>
      <article className="rounded-xl border border-slate-800 bg-slate-900 p-5"><div className="flex items-center gap-3"><Route className="text-cyan-300" /><h2 className="font-semibold">Locked action mapping</h2></div><div className="mt-3 space-y-2 font-mono text-xs text-slate-300">{(guide?.mapping ?? []).map((item) => <div key={item.source} className="rounded bg-slate-950 p-2">{item.source} → {item.target}</div>)}<div className="rounded bg-cyan-950/40 p-2">{guide?.gripper.source ?? "gripper.pos"} → {guide?.gripper.target ?? "amazinghand_motion.pos"}</div></div><p className="mt-3 text-xs text-slate-400">The gripper selects one complete AmazingHand pose: {(guide?.gripper.motions ?? []).map((motion) => motion.name.replace("_", " ")).join(", ")}.</p></article></section>
    <section className="rounded-xl border border-slate-800 bg-slate-900 p-5"><div className="flex items-center gap-3"><Settings2 className="text-cyan-300" /><div><h2 className="font-semibold">Follower: {guide?.follower.device_type ?? "Loading…"}</h2><p className="text-sm text-cyan-300">First target: {guide?.follower.first_target ?? "MuJoCo SuperArm + AmazingHand"}</p></div></div><div className="mt-4 grid gap-4 md:grid-cols-2"><div><h3 className="text-sm font-medium">Real SuperArm configuration</h3><p className="mt-1 text-sm text-slate-400">Before any DM4340P torque is enabled, copy and complete this local hardware configuration. Its placeholders are intentionally invalid.</p><code className="mt-3 block break-all rounded bg-slate-950 p-3 text-xs text-cyan-200">{guide?.follower.hardware_config_template ?? "Loading…"}</code></div><div><h3 className="text-sm font-medium">Required five-joint calibration</h3><p className="mt-1 text-xs text-slate-400">Each joint must use {guide?.follower.arm_calibration.format ?? "[direction, zero_offset_rad]"}.</p><div className="mt-3 flex flex-wrap gap-2">{(guide?.follower.arm_calibration.joints ?? []).map((joint) => <code key={joint} className="rounded bg-slate-950 px-2 py-1 text-xs text-cyan-200">{joint}</code>)}</div><ul className="mt-3 space-y-1 text-xs text-slate-400">{(guide?.follower.arm_calibration.requires ?? []).map((item) => <li key={item}>• {item}</li>)}</ul><Button className="mt-4" variant="outline" onClick={() => navigate("/superarm-follower-calibration")}>Open SuperArm calibration wizard</Button></div></div></section>
    <section className="rounded-xl border border-slate-800 bg-slate-900 p-5"><div className="mb-4 flex items-center gap-2"><Cable className="text-cyan-300" /><h2 className="font-semibold">Step-by-step dry run</h2></div><ol className="space-y-3">{(guide?.steps ?? ["Loading server guide…"]).map((step, index) => <li key={step} className="flex gap-3 rounded-lg bg-slate-950 p-3 text-sm"><span className="font-semibold text-cyan-300">{index + 1}</span><span>{step}</span></li>)}</ol><div className="mt-5 flex flex-wrap gap-3"><Button onClick={() => navigate("/")}>Open dashboard recording</Button><Button variant="outline" onClick={() => navigate("/hardware-setup")}>Open real follower checklist</Button></div></section>
  </main></div>;
}
