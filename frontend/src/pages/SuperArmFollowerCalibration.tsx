import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Activity, ArrowLeft, Download, Play, ShieldAlert, Square, Target } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useApi } from "@/contexts/ApiContext";

const JOINTS = ["joint_rev_1", "joint_rev_2", "joint_rev_3", "joint_rev_4", "joint_rev_5"] as const;

type JointDraft = {
  sendId: string;
  receiveId: string;
  direction: string;
  zeroOffset: string;
  lowerLimit: string;
  upperLimit: string;
  kp: string;
  kd: string;
};

type Preview = { filename: string; yaml: string; connects_hardware: boolean; motion_authorized: boolean };
type CalibrationStatus = { calibration_active: boolean; torque_enabled: boolean; zero_captured: boolean; message: string; error: string | null; recorded_ranges: Record<string, { min: number; max: number; current: number }> };

const emptyJoint = (): JointDraft => ({
  sendId: "",
  receiveId: "",
  direction: "",
  zeroOffset: "",
  lowerLimit: "",
  upperLimit: "",
  kp: "",
  kd: "",
});

export default function SuperArmFollowerCalibration() {
  const navigate = useNavigate();
  const { baseUrl, fetchWithHeaders } = useApi();
  const [armPort, setArmPort] = useState("can0");
  const [handPort, setHandPort] = useState("/dev/ttyACM0");
  const [handSpeed, setHandSpeed] = useState("3");
  const [joints, setJoints] = useState<Record<string, JointDraft>>(() =>
    Object.fromEntries(JOINTS.map((joint) => [joint, emptyJoint()])),
  );
  const [confirmed, setConfirmed] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [liveStatus, setLiveStatus] = useState<CalibrationStatus | null>(null);
  const [liveError, setLiveError] = useState("");
  const calibrationActiveRef = useRef(false);

  const completion = useMemo(
    () => JOINTS.filter((joint) => Object.values(joints[joint]).every((value) => value.trim() !== "")).length,
    [joints],
  );

  const updateJoint = (joint: string, key: keyof JointDraft, value: string) => {
    setJoints((current) => ({ ...current, [joint]: { ...current[joint], [key]: value } }));
    setPreview(null);
  };

  const pollLiveStatus = useCallback(async () => {
    try {
      const response = await fetchWithHeaders(`${baseUrl}/api/superarm/calibration`);
      if (!response.ok) return;
      const status = await response.json() as CalibrationStatus;
      setLiveStatus(status);
      if (status.error) setLiveError(status.error);
    } catch (cause) {
      setLiveError(cause instanceof Error ? cause.message : "Could not read calibration status.");
    }
  }, [baseUrl, fetchWithHeaders]);

  useEffect(() => {
    if (!liveStatus?.calibration_active) return;
    const timer = window.setInterval(() => void pollLiveStatus(), 150);
    return () => window.clearInterval(timer);
  }, [liveStatus?.calibration_active, pollLiveStatus]);

  useEffect(() => {
    calibrationActiveRef.current = Boolean(liveStatus?.calibration_active);
  }, [liveStatus?.calibration_active]);

  useEffect(() => () => {
    if (calibrationActiveRef.current) {
      void fetchWithHeaders(`${baseUrl}/api/superarm/calibration/stop`, { method: "POST" });
    }
  }, [baseUrl, fetchWithHeaders]);

  const startLiveCalibration = async () => {
    setLiveError("");
    try {
      const response = await fetchWithHeaders(`${baseUrl}/api/superarm/calibration/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          arm_port: armPort,
          arm_motor_config: Object.fromEntries(JOINTS.map((joint) => [joint, [Number(joints[joint].sendId), Number(joints[joint].receiveId)]])),
          confirmed_torque_disabled_area: true,
        }),
      });
      const data = await response.json() as CalibrationStatus | { detail?: string };
      if (!response.ok) throw new Error("detail" in data ? data.detail || "Could not start calibration." : "Could not start calibration.");
      setLiveStatus(data as CalibrationStatus);
    } catch (cause) {
      setLiveError(cause instanceof Error ? cause.message : "Could not start calibration.");
    }
  };

  const liveAction = async (path: "capture-zero" | "stop") => {
    setLiveError("");
    try {
      const response = await fetchWithHeaders(`${baseUrl}/api/superarm/calibration/${path}`, { method: "POST" });
      const data = await response.json() as CalibrationStatus | { detail?: string };
      if (!response.ok) throw new Error("detail" in data ? data.detail || "Calibration action failed." : "Calibration action failed.");
      setLiveStatus(data as CalibrationStatus);
    } catch (cause) {
      setLiveError(cause instanceof Error ? cause.message : "Calibration action failed.");
    }
  };

  const generate = async () => {
    setBusy(true);
    setError("");
    setPreview(null);
    const number = (value: string) => Number(value);
    const payload = {
      arm_port: armPort,
      hand_port: handPort,
      arm_motor_config: Object.fromEntries(JOINTS.map((joint) => [joint, [number(joints[joint].sendId), number(joints[joint].receiveId)]])),
      arm_joint_calibration: Object.fromEntries(JOINTS.map((joint) => [joint, [number(joints[joint].direction), number(joints[joint].zeroOffset)]])),
      arm_joint_limits_deg: Object.fromEntries(JOINTS.map((joint) => [joint, [number(joints[joint].lowerLimit), number(joints[joint].upperLimit)]])),
      arm_position_kp: JOINTS.map((joint) => number(joints[joint].kp)),
      arm_position_kd: JOINTS.map((joint) => number(joints[joint].kd)),
      hand_speed: number(handSpeed),
      confirmed_measured: confirmed,
    };
    try {
      const response = await fetchWithHeaders(`${baseUrl}/api/superarm/hardware-config/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await response.json() as Preview | { detail?: string };
      if (!response.ok) throw new Error("detail" in data ? data.detail || "Calibration values were rejected." : "Calibration values were rejected.");
      setPreview(data as Preview);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not validate the configuration.");
    } finally {
      setBusy(false);
    }
  };

  const download = () => {
    if (!preview) return;
    const url = URL.createObjectURL(new Blob([preview.yaml], { type: "application/x-yaml" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = preview.filename;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800 px-4 py-3"><div className="mx-auto flex max-w-7xl items-center gap-3"><Button variant="ghost" size="icon" onClick={() => navigate("/hardware-setup")} aria-label="Back"><ArrowLeft /></Button><div><h1 className="text-xl font-semibold">SuperArm follower calibration</h1><p className="text-xs text-cyan-400">Five DM4340P joints + AmazingHand configuration, without motor connection</p></div></div></header>
      <main className="mx-auto max-w-7xl space-y-5 p-4">
        <section className="rounded-xl border border-amber-700/70 bg-amber-950/30 p-4"><div className="flex gap-3"><ShieldAlert className="mt-0.5 shrink-0 text-amber-300" /><div><h2 className="font-semibold text-amber-200">Measurement and config wizard only</h2><p className="mt-1 text-sm text-amber-100/80">This wizard never opens CAN, serial, or torque. Measure the arm with torque disabled, validate the values here, then save the downloaded YAML outside this repository. SO-101 follower calibration does not apply to SuperArm.</p></div></div></section>
        <section className="grid gap-4 md:grid-cols-3"><Step number="1" title="Discover safely" detail="With torque disabled, identify each SuperArm CAN send/receive ID pair." /><Step number="2" title="Measure five joints" detail="Record direction, zero offset, safe limits, and gains for every SuperArm joint." /><Step number="3" title="Validate and save" detail="Confirm measurements, generate YAML, then use the separate hardware adapter checklist." /></section>
        <section className="rounded-xl border border-slate-800 bg-slate-900 p-5"><h2 className="font-semibold">Bus settings</h2><div className="mt-4 grid gap-3 md:grid-cols-3"><Field label="Arm CAN interface"><Input value={armPort} onChange={(event) => { setArmPort(event.target.value); setPreview(null); }} placeholder="can0" /></Field><Field label="AmazingHand serial port"><Input value={handPort} onChange={(event) => { setHandPort(event.target.value); setPreview(null); }} placeholder="/dev/ttyACM0" /></Field><Field label="AmazingHand speed (1–6)"><Input type="number" min="1" max="6" value={handSpeed} onChange={(event) => { setHandSpeed(event.target.value); setPreview(null); }} /></Field></div></section>
        <section className="rounded-xl border border-slate-800 bg-slate-900 p-5"><div className="flex flex-wrap items-baseline justify-between gap-2"><div><h2 className="font-semibold">Five SuperArm arm-joint measurements</h2><p className="text-sm text-slate-400">Completed rows: {completion}/5. Direction must be +1 or -1; offset is in SuperArm joint radians.</p></div><span className="rounded-full bg-cyan-950 px-3 py-1 text-xs text-cyan-200">DM4340P / CAN-FD</span></div><div className="mt-4 space-y-4">{JOINTS.map((joint) => <JointRow key={joint} joint={joint} value={joints[joint]} onChange={updateJoint} />)}</div></section>
        <section className="rounded-xl border border-slate-800 bg-slate-900 p-5"><div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="flex items-center gap-2 font-semibold"><Activity className="h-5 w-5 text-cyan-300" />Live follower calibration</h2><p className="text-sm text-slate-400">Same sequence as the SO-101 calibration page: connect, reference pose, capture zero, manually sweep every joint, then save the resulting SuperArm config.</p></div><span className="rounded-full bg-amber-950 px-3 py-1 text-xs text-amber-200">Torque remains disabled</span></div><div className="mt-4 flex flex-wrap gap-3">{!liveStatus?.calibration_active ? <Button onClick={() => void startLiveCalibration()}><Play className="mr-2 h-4 w-4" />Start torque-disabled calibration</Button> : <><Button onClick={() => void liveAction("capture-zero")} disabled={liveStatus.zero_captured}><Target className="mr-2 h-4 w-4" />{liveStatus.zero_captured ? "Zero captured" : "Capture reference zero"}</Button><Button variant="destructive" onClick={() => void liveAction("stop")}><Square className="mr-2 h-4 w-4" />Finish and disconnect</Button></>}</div>{liveError && <p className="mt-3 rounded border border-red-700 bg-red-950/40 p-3 text-sm text-red-200">{liveError}</p>}{liveStatus && <div className="mt-4 rounded-lg bg-slate-950 p-4"><p className="text-sm text-cyan-200">{liveStatus.message}</p><div className="mt-4 grid gap-3 md:grid-cols-2">{Object.entries(liveStatus.recorded_ranges).map(([joint, range]) => <div key={joint} className="rounded border border-slate-800 p-3"><div className="flex justify-between font-mono text-xs"><span>{joint}</span><span>{range.current.toFixed(2)}°</span></div><div className="mt-2 h-2 overflow-hidden rounded bg-slate-800"><div className="h-full bg-cyan-500" style={{ width: `${Math.min(100, Math.abs(range.max - range.min) / 1.8)}%` }} /></div><p className="mt-1 text-xs text-slate-400">min {range.min.toFixed(2)}° · max {range.max.toFixed(2)}° · range {(range.max - range.min).toFixed(2)}°</p></div>)}</div></div>}</section>
        <section className="rounded-xl border border-slate-800 bg-slate-900 p-5"><label className="flex cursor-pointer items-start gap-3 text-sm"><input type="checkbox" checked={confirmed} onChange={(event) => { setConfirmed(event.target.checked); setPreview(null); }} className="mt-1 h-4 w-4 accent-cyan-400" /><span>I confirm that all CAN IDs, directions, zero offsets, limits, and gains above were measured from this specific SuperArm with torque disabled where required.</span></label><Button className="mt-4" onClick={() => void generate()} disabled={busy}>{busy ? "Validating…" : "Validate and generate local YAML"}</Button>{error && <p className="mt-3 rounded border border-red-700 bg-red-950/40 p-3 text-sm text-red-200">{error}</p>}{preview && <div className="mt-4 rounded-lg border border-emerald-700 bg-emerald-950/30 p-4"><p className="text-sm text-emerald-200">Configuration syntax and all five calibration records are valid. No hardware was connected and this does not authorize motion.</p><Button className="mt-3" variant="outline" onClick={download}><Download className="mr-2 h-4 w-4" />Download {preview.filename}</Button><pre className="mt-4 max-h-72 overflow-auto rounded bg-slate-950 p-3 text-xs text-slate-300">{preview.yaml}</pre></div>}</section>
      </main>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="space-y-2 text-sm text-slate-300"><span>{label}</span>{children}</label>;
}

function Step({ number, title, detail }: { number: string; title: string; detail: string }) {
  return <article className="rounded-xl border border-slate-800 bg-slate-900 p-4"><span className="text-lg font-bold text-cyan-300">{number}</span><h2 className="mt-1 font-semibold">{title}</h2><p className="mt-1 text-sm text-slate-400">{detail}</p></article>;
}

function JointRow({ joint, value, onChange }: { joint: string; value: JointDraft; onChange: (joint: string, key: keyof JointDraft, value: string) => void }) {
  const input = (key: keyof JointDraft, label: string, step = "any") => <Field label={label}><Input type="number" step={step} value={value[key]} onChange={(event) => onChange(joint, key, event.target.value)} /></Field>;
  return <article className="rounded-lg bg-slate-950 p-4"><h3 className="mb-3 font-mono text-sm text-cyan-200">{joint}</h3><div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{input("sendId", "CAN send ID", "1")}{input("receiveId", "CAN receive ID", "1")}{input("direction", "Direction (+1 / -1)", "1")}{input("zeroOffset", "Zero offset (rad)")}{input("lowerLimit", "Lower limit (deg)")}{input("upperLimit", "Upper limit (deg)")}{input("kp", "position_kp")}{input("kd", "position_kd")}</div></article>;
}
