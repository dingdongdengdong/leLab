import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Activity,
  ArrowLeft,
  CircleStop,
  Hand,
  Pause,
  Play,
  Power,
  Save,
  Trash2,
  Unplug,
} from "lucide-react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import SuperArmUrdfViewer from "@/components/SuperArmUrdfViewer";
import { Switch } from "@/components/ui/switch";
import { useApi } from "@/contexts/ApiContext";
import { useToast } from "@/hooks/use-toast";
import {
  baseSideToRaw,
  clampSuperArm as clamp,
  formatSuperArmMetric as pretty,
  keyboardFocusProtected,
  reorderSequenceStep,
} from "@/lib/superarmControl";
import { extractVisualPose, MjcfVisualPose } from "@/lib/mjcfVisualLayer";

type FingerName = "pointer" | "middle" | "ring" | "thumb";
type Pair = [number, number];
type Runtime = "mujoco" | "hybrid_serial";

const ARM_JOINTS = ["joint_rev_1", "joint_rev_2", "joint_rev_3", "joint_rev_4", "joint_rev_5"];
const FINGERS: FingerName[] = ["pointer", "middle", "ring", "thumb"];
const UPSTREAM_KEYS: Record<string, FingerName> = {
  "1": "ring",
  "2": "middle",
  "3": "pointer",
  "4": "thumb",
};

const zeroArm = Object.fromEntries(ARM_JOINTS.map((name) => [name, 0]));
const openHand = Object.fromEntries(FINGERS.map((name) => [name, [0, 0] as Pair])) as Record<FingerName, Pair>;
const speedHand = Object.fromEntries(FINGERS.map((name) => [name, [3, 3] as Pair])) as Record<FingerName, Pair>;

interface TelemetryPoint {
  time: string;
  position: number | null;
  target: number | null;
}

interface TelemetryMetric {
  position?: number | null;
  target?: number | null;
  goal?: number | null;
  speed?: number | null;
  load?: number | null;
  voltage?: number | null;
  temperature?: number | null;
  status?: number | null;
  moving?: boolean | null;
  estimated_current_ma?: number | null;
  [key: string]: number | boolean | null | undefined;
}

interface RuntimeTelemetry {
  arm?: Record<string, TelemetryMetric>;
  hand?: Record<string, TelemetryMetric>;
  serial_hand?: Record<string, TelemetryMetric>;
}

interface SavedPose {
  arm_rad?: Record<string, number>;
  hand_deg?: Partial<Record<FingerName, Pair>>;
}

type SequenceStep =
  | { pose: string; transition_s?: number; hold_s?: number; hand_speed?: number | number[] }
  | { sleep_s: number };

interface RuntimeStatus {
  connected: boolean;
  emergency_stopped: boolean;
  runtime: Runtime | null;
}

const errorMessage = (error: unknown) => error instanceof Error ? error.message : String(error);

const SuperArm = () => {
  const navigate = useNavigate();
  const { baseUrl, wsBaseUrl, fetchWithHeaders } = useApi();
  const { toast } = useToast();
  const [runtime, setRuntime] = useState<Runtime>("mujoco");
  const [serialPort, setSerialPort] = useState("/dev/ttyACM0");
  const [availablePorts, setAvailablePorts] = useState<string[]>([]);
  const [connected, setConnected] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [emergencyStopped, setEmergencyStopped] = useState(false);
  const [statusText, setStatusText] = useState("Disconnected");
  const [arm, setArm] = useState<Record<string, number>>({ ...zeroArm });
  const [hand, setHand] = useState<Record<FingerName, Pair>>({ ...openHand });
  const [speeds, setSpeeds] = useState<Record<FingerName, Pair>>({ ...speedHand });
  const [fingerModes, setFingerModes] = useState<Record<FingerName, "auto" | "raw">>(
    Object.fromEntries(FINGERS.map((finger) => [finger, "auto"])) as Record<FingerName, "auto" | "raw">,
  );
  const [globalSpeed, setGlobalSpeed] = useState(3);
  const [liveMode, setLiveMode] = useState(false);
  const [selectedFinger, setSelectedFinger] = useState<FingerName>("ring");
  const [telemetry, setTelemetry] = useState<RuntimeTelemetry>({});
  const [visualPose, setVisualPose] = useState<MjcfVisualPose | null>(null);
  const [history, setHistory] = useState<TelemetryPoint[]>([]);
  const [chartChannel, setChartChannel] = useState("joint_rev_1");
  const [poses, setPoses] = useState<Record<string, SavedPose>>({});
  const [sequences, setSequences] = useState<Record<string, { steps: SequenceStep[] }>>({});
  const [poseName, setPoseName] = useState("home");
  const [sequenceName, setSequenceName] = useState("demo");
  const [sequenceSteps, setSequenceSteps] = useState<SequenceStep[]>([
    { pose: "home", transition_s: 1, hold_s: 0.5, hand_speed: 3 },
  ]);
  const liveTimer = useRef<number | null>(null);
  const socket = useRef<WebSocket | null>(null);

  const request = useCallback(
    async <T,>(path: string, init: RequestInit = {}): Promise<T> => {
      const response = await fetchWithHeaders(`${baseUrl}${path}`, init);
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Request failed (${response.status})`);
      }
      if (response.status === 204) return null as T;
      return response.json() as Promise<T>;
    },
    [baseUrl, fetchWithHeaders],
  );

  const refreshPrograms = useCallback(async () => {
    const [nextPoses, nextSequences] = await Promise.all([
      request<Record<string, SavedPose>>("/api/superarm/poses"),
      request<Record<string, { steps: SequenceStep[] }>>("/api/superarm/sequences"),
    ]);
    setPoses(nextPoses);
    setSequences(nextSequences);
  }, [request]);

  useEffect(() => {
    request<{ runtimes: { hybrid_serial: { serial_ports: string[] } } }>("/api/superarm/capabilities")
      .then((data) => setAvailablePorts(data.runtimes.hybrid_serial.serial_ports || []))
      .catch((error) => setStatusText(error.message));
    request<RuntimeStatus>("/api/superarm/session")
      .then((status) => {
        setConnected(status.connected);
        setEmergencyStopped(status.emergency_stopped);
        if (status.runtime) setRuntime(status.runtime);
        setStatusText(status.connected ? `Connected: ${status.runtime}` : "Disconnected");
      })
      .catch((error) => setStatusText(error.message));
    refreshPrograms().catch(() => undefined);
  }, [request, refreshPrograms]);

  useEffect(() => {
    if (!connected) return;
    const ws = new WebSocket(`${wsBaseUrl}/ws/superarm`);
    socket.current = ws;
    ws.onmessage = (event) => {
      const message = JSON.parse(event.data);
      const nextVisualPose = extractVisualPose(message);
      if (nextVisualPose) setVisualPose(nextVisualPose);
      if (typeof message.connected === "boolean") setConnected(message.connected);
      if (typeof message.emergency_stopped === "boolean") setEmergencyStopped(message.emergency_stopped);
      if (message.error) setStatusText(message.error);
      if (message.type === "state" && message.state) {
        setTelemetry(message.state);
        const runtimeState = message.state;
        const channel = runtimeState.arm?.[chartChannel] || runtimeState.hand?.[chartChannel];
        if (channel) {
          setHistory((previous) => [
            ...previous.slice(-119),
            {
              time: new Date().toLocaleTimeString(),
              position: typeof channel.position === "number" ? channel.position : null,
              target: typeof channel.target === "number" ? channel.target : null,
            },
          ]);
        }
      }
    };
    ws.onclose = () => {
      socket.current = null;
      setLiveMode(false);
    };
    return () => ws.close();
  }, [connected, wsBaseUrl, chartChannel]);

  const disconnect = useCallback(async () => {
    setLiveMode(false);
    try {
      await request("/api/superarm/session", { method: "DELETE" });
    } finally {
      setConnected(false);
      setStatusText("Disconnected");
    }
  }, [request]);

  useEffect(() => {
    const onVisibility = () => {
      if (document.hidden) setLiveMode(false);
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  useEffect(() => () => {
    if (connected) {
      const body = new Blob([JSON.stringify({ active: true })], { type: "application/json" });
      navigator.sendBeacon(`${baseUrl}/api/superarm/emergency-stop`, body);
    }
  }, [baseUrl, connected]);

  const actionPayload = useCallback(
    (source: "staged" | "live" | "keyboard" = "staged") => ({
      arm_rad: arm,
      hand_deg: hand,
      hand_speed: speeds,
      source,
    }),
    [arm, hand, speeds],
  );

  const apply = useCallback(
    async (source: "staged" | "live" | "keyboard" = "staged") => {
      if (!connected || emergencyStopped) return;
      try {
        await request("/api/superarm/action", {
          method: "PUT",
          body: JSON.stringify(actionPayload(source)),
        });
      } catch (error: unknown) {
        const message = errorMessage(error);
        if (!message.includes("20 Hz")) {
          setLiveMode(false);
          toast({ title: "Command rejected", description: message, variant: "destructive" });
        }
      }
    },
    [actionPayload, connected, emergencyStopped, request, toast],
  );

  useEffect(() => {
    if (!liveMode) return;
    if (liveTimer.current) window.clearTimeout(liveTimer.current);
    liveTimer.current = window.setTimeout(() => apply("live"), 50);
    return () => {
      if (liveTimer.current) window.clearTimeout(liveTimer.current);
    };
  }, [arm, hand, speeds, liveMode, apply]);

  useEffect(() => {
    if (!liveMode) return;
    const timeout = window.setTimeout(() => setLiveMode(false), 10_000);
    return () => window.clearTimeout(timeout);
  }, [arm, hand, speeds, liveMode]);

  const updatePair = (finger: FingerName, index: number, value: number) => {
    setHand((previous) => {
      const pair = [...previous[finger]] as Pair;
      pair[index] = clamp(value, -40, 110);
      return { ...previous, [finger]: pair };
    });
  };

  const setAuto = (finger: FingerName, base: number, side: number) => {
    setHand((previous) => ({
      ...previous,
      [finger]: baseSideToRaw(base, side),
    }));
  };

  const setAll = (value: Pair) => {
    setHand(Object.fromEntries(FINGERS.map((finger) => [finger, [...value] as Pair])) as Record<FingerName, Pair>);
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const element = event.target as HTMLElement;
      if (keyboardFocusProtected(element.tagName, element.isContentEditable)) return;
      if (UPSTREAM_KEYS[event.key]) {
        setSelectedFinger(UPSTREAM_KEYS[event.key]);
        return;
      }
      const step = event.ctrlKey ? 10 : event.shiftKey ? 5 : 1;
      let next = [...hand[selectedFinger]] as Pair;
      if (event.key === "ArrowUp") next = [next[0] + step, next[1] + step];
      else if (event.key === "ArrowDown") next = [next[0] - step, next[1] - step];
      else if (event.key === "ArrowRight") next = [next[0] + step, next[1] - step];
      else if (event.key === "ArrowLeft") next = [next[0] - step, next[1] + step];
      else if (event.key.toLowerCase() === "q") next = [110, 110];
      else if (event.key.toLowerCase() === "e") next = [0, 0];
      else if (event.key.toLowerCase() === "c") next = [(next[0] + next[1]) / 2, (next[0] + next[1]) / 2];
      else return;
      event.preventDefault();
      const nextHand = {
        ...hand,
        [selectedFinger]: [clamp(next[0], -40, 110), clamp(next[1], -40, 110)] as Pair,
      };
      setHand(nextHand);
      if (connected && !emergencyStopped) {
        request("/api/superarm/action", {
          method: "PUT",
          body: JSON.stringify({ arm_rad: arm, hand_deg: nextHand, hand_speed: speeds, source: "keyboard" }),
        }).catch((error) => setStatusText(error.message));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [hand, selectedFinger, connected, emergencyStopped, request, arm, speeds]);

  const connect = async () => {
    setConnecting(true);
    try {
      const data = await request<RuntimeStatus>("/api/superarm/session", {
        method: "POST",
        body: JSON.stringify({ runtime, serial_port: serialPort }),
      });
      setConnected(data.connected);
      setEmergencyStopped(data.emergency_stopped);
      setStatusText(data.connected ? `Connected: ${runtime}` : "Connection failed");
    } catch (error: unknown) {
      const message = errorMessage(error);
      setStatusText(message);
      toast({ title: "Runtime unavailable", description: message, variant: "destructive" });
    } finally {
      setConnecting(false);
    }
  };

  const emergencyStop = async () => {
    const active = !emergencyStopped;
    const data = await request<RuntimeStatus>("/api/superarm/emergency-stop", {
      method: "POST",
      body: JSON.stringify({ active }),
    });
    setEmergencyStopped(data.emergency_stopped);
    setLiveMode(false);
  };

  const savePose = async () => {
    await request(`/api/superarm/poses/${encodeURIComponent(poseName)}`, {
      method: "PUT",
      body: JSON.stringify({ arm_rad: arm, hand_deg: hand }),
    });
    await refreshPrograms();
  };

  const saveSequence = async () => {
    await request(`/api/superarm/sequences/${encodeURIComponent(sequenceName)}`, {
      method: "PUT",
      body: JSON.stringify({ steps: sequenceSteps }),
    });
    await refreshPrograms();
  };

  const selectedMetrics = useMemo(() => {
    if (chartChannel.startsWith("joint_rev")) return telemetry.arm?.[chartChannel] || {};
    return telemetry.serial_hand?.[chartChannel] || telemetry.hand?.[chartChannel] || {};
  }, [telemetry, chartChannel]);

  const urdfJointPositions = useMemo(
    () => Object.fromEntries(
      ARM_JOINTS.map((joint) => {
        const measured = telemetry.arm?.[joint]?.position;
        return [joint, typeof measured === "number" ? measured : arm[joint]];
      }),
    ),
    [arm, telemetry.arm],
  );

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="sticky top-0 z-20 border-b border-slate-800 bg-slate-950/95 px-4 py-3 backdrop-blur">
        <div className="mx-auto flex max-w-[1600px] flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <Button variant="ghost" size="icon" onClick={() => navigate("/")} aria-label="Back">
              <ArrowLeft />
            </Button>
            <div>
              <h1 className="text-xl font-semibold">SuperArm + Hand</h1>
              <p className="text-xs text-cyan-400">Source arm · official closed-loop MJCF · 13 actuators</p>
            </div>
          </div>
          <Button variant="outline" onClick={() => navigate("/hardware-setup")} className="border-cyan-700 text-cyan-200 hover:bg-cyan-950">Hardware setup</Button>
          <Button
            onClick={emergencyStop}
            className={`min-w-52 text-base font-bold ${emergencyStopped ? "bg-red-700 animate-pulse" : "bg-red-600 hover:bg-red-500"}`}
          >
            <CircleStop className="mr-2" /> {emergencyStopped ? "RESET EMERGENCY STOP" : "EMERGENCY STOP"}
          </Button>
        </div>
      </header>

      <main className="mx-auto grid max-w-[1600px] gap-4 p-4 xl:grid-cols-[minmax(0,1.25fr)_minmax(460px,.75fr)]">
        <section className="space-y-4">
          <div className="rounded-xl border border-slate-800 bg-slate-900 p-4">
            <div className="flex flex-wrap items-end gap-3">
              <label className="grid gap-1 text-sm">
                Runtime
                <select value={runtime} onChange={(event) => setRuntime(event.target.value as Runtime)} disabled={connected} className="rounded border border-slate-700 bg-slate-950 p-2">
                  <option value="mujoco">MuJoCo (default)</option>
                  <option value="hybrid_serial">Hybrid serial</option>
                </select>
              </label>
              {runtime === "hybrid_serial" && (
                <label className="grid gap-1 text-sm">
                  Serial port
                  <Input value={serialPort} onChange={(event) => setSerialPort(event.target.value)} list="superarm-ports" className="w-48 bg-slate-950" />
                  <datalist id="superarm-ports">{availablePorts.map((port) => <option value={port} key={port} />)}</datalist>
                </label>
              )}
              {!connected ? (
                <Button onClick={connect} disabled={connecting} className="bg-emerald-600 hover:bg-emerald-500">
                  <Power className="mr-2 h-4 w-4" /> {connecting ? "Starting…" : "Connect"}
                </Button>
              ) : (
                <Button onClick={disconnect} variant="outline" className="border-slate-600">
                  <Unplug className="mr-2 h-4 w-4" /> Disconnect
                </Button>
              )}
              <span className={`rounded-full px-3 py-1 text-sm ${connected ? "bg-emerald-950 text-emerald-300" : "bg-slate-800 text-slate-400"}`}>{statusText}</span>
              <label className="ml-auto flex items-center gap-2 text-sm">
                Live (20 Hz)
                <Switch checked={liveMode} onCheckedChange={setLiveMode} disabled={!connected || emergencyStopped} />
              </label>
            </div>
          </div>

          <div className="grid gap-4 2xl:grid-cols-2">
            <div className="overflow-hidden rounded-xl border border-slate-800 bg-slate-900">
              <div className="border-b border-slate-800 px-4 py-3">
                <h2 className="font-semibold">LeLab URDF showroom</h2>
                <p className="text-xs text-slate-400">Browser-side kinematic reference using LeLab’s standard Three.js viewer.</p>
              </div>
              <SuperArmUrdfViewer jointPositions={urdfJointPositions} visualPose={visualPose} />
            </div>
            <div className="overflow-hidden rounded-xl border border-slate-800 bg-slate-900">
              <div className="border-b border-slate-800 px-4 py-3">
                <h2 className="font-semibold">MuJoCo physics</h2>
                <p className="text-xs text-slate-400">Server-rendered closed-loop hand and full arm assembly at 15 FPS.</p>
              </div>
              <div className="aspect-[4/3] bg-black">
                {connected ? (
                  <img src={`${baseUrl}/api/superarm/video`} alt="Live MuJoCo SuperArm and Hand" className="h-full w-full object-contain" />
                ) : (
                  <div className="flex h-full flex-col items-center justify-center text-slate-500"><Hand className="mb-3 h-16 w-16" />Connect MuJoCo to start the 640×480 stream</div>
                )}
              </div>
            </div>
          </div>

          <div className="rounded-xl border border-slate-800 bg-slate-900 p-4">
            <div className="mb-3 flex items-center justify-between"><h2 className="font-semibold">Source arm · radians</h2><span className="text-xs text-slate-400">Conservative limits −1.57…1.57</span></div>
            <div className="grid gap-3">
              {ARM_JOINTS.map((joint) => (
                <label key={joint} className="grid grid-cols-[110px_1fr_64px] items-center gap-3 text-sm">
                  <span>{joint}</span>
                  <input type="range" min="-1.57" max="1.57" step="0.01" value={arm[joint]} onChange={(event) => setArm({ ...arm, [joint]: Number(event.target.value) })} />
                  <span className="font-mono text-cyan-300">{arm[joint].toFixed(2)}</span>
                </label>
              ))}
            </div>
          </div>

          <div className="rounded-xl border border-slate-800 bg-slate-900 p-4">
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <h2 className="mr-auto font-semibold">Hand</h2>
              <span className="text-sm">Global speed</span>
              <input type="range" min="1" max="6" value={globalSpeed} onChange={(event) => {
                const next = Number(event.target.value); setGlobalSpeed(next); setSpeeds(Object.fromEntries(FINGERS.map((finger) => [finger, [next, next]])) as Record<FingerName, Pair>);
              }} />
              <span>{globalSpeed}</span>
              <Button size="sm" variant="outline" onClick={() => setAll([0, 0])}>Open</Button>
              <Button size="sm" variant="outline" onClick={() => setAll([110, 110])}>Close</Button>
              <Button size="sm" variant="outline" onClick={() => setAll([55, 55])}>Center</Button>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              {FINGERS.map((finger) => {
                const base = (hand[finger][0] + hand[finger][1]) / 2;
                const side = (hand[finger][0] - hand[finger][1]) / 2;
                return (
                  <div key={finger} className={`rounded-lg border p-3 ${selectedFinger === finger ? "border-cyan-500 bg-cyan-950/20" : "border-slate-700 bg-slate-950"}`}>
                    <div className="mb-3 flex items-center justify-between">
                      <button className="font-semibold capitalize" onClick={() => setSelectedFinger(finger)}>{finger}</button>
                      <div className="flex rounded bg-slate-800 p-0.5 text-xs">
                        {(["auto", "raw"] as const).map((mode) => <button key={mode} onClick={() => setFingerModes({ ...fingerModes, [finger]: mode })} className={`rounded px-2 py-1 ${fingerModes[finger] === mode ? "bg-cyan-600" : ""}`}>{mode}</button>)}
                      </div>
                    </div>
                    {fingerModes[finger] === "auto" ? (
                      <>
                        <SliderRow label="Base" value={base} min={0} max={110} onChange={(value) => setAuto(finger, value, side)} />
                        <SliderRow label="Side" value={side} min={-40} max={40} onChange={(value) => setAuto(finger, base, value)} />
                      </>
                    ) : (
                      <>
                        <SliderRow label="Servo 1" value={hand[finger][0]} min={-40} max={110} onChange={(value) => updatePair(finger, 0, value)} />
                        <SliderRow label="Servo 2" value={hand[finger][1]} min={-40} max={110} onChange={(value) => updatePair(finger, 1, value)} />
                      </>
                    )}
                    <SliderRow label="Speed" value={speeds[finger][0]} min={1} max={6} step={1} onChange={(value) => setSpeeds({ ...speeds, [finger]: [value, value] })} />
                    <div className="mt-2 grid grid-cols-4 gap-1">
                      <MiniButton text="Open" onClick={() => setHand({ ...hand, [finger]: [0, 0] })} />
                      <MiniButton text="Close" onClick={() => setHand({ ...hand, [finger]: [110, 110] })} />
                      <MiniButton text="Center" onClick={() => setHand({ ...hand, [finger]: [55, 55] })} />
                      <MiniButton text="Mimic" onClick={() => setHand({ ...hand, [finger]: [...hand.pointer] as Pair })} />
                    </div>
                  </div>
                );
              })}
            </div>
            <div className="mt-4 flex items-center justify-between gap-3 rounded-lg bg-slate-950 p-3">
              <p className="text-xs text-slate-400">Keyboard: 1–4 Ring/Middle/Pointer/Thumb · arrows move · Shift/Ctrl precision · Q close · E open · C center</p>
              <Button onClick={() => apply("staged")} disabled={!connected || emergencyStopped} className="min-w-40 bg-cyan-500 text-slate-950 hover:bg-cyan-400">Apply staged</Button>
            </div>
          </div>
        </section>

        <aside className="space-y-4">
          <div className="rounded-xl border border-slate-800 bg-slate-900 p-4">
            <div className="mb-3 flex items-center gap-2"><Activity className="h-4 w-4 text-cyan-400" /><h2 className="font-semibold">Telemetry</h2></div>
            <select value={chartChannel} onChange={(event) => { setChartChannel(event.target.value); setHistory([]); }} className="mb-3 w-full rounded border border-slate-700 bg-slate-950 p-2 text-sm">
              {ARM_JOINTS.map((name) => <option key={name}>{name}</option>)}
              {Array.from({ length: 4 }, (_, finger) => [1, 2].map((motor) => <option key={`finger${finger + 1}_motor${motor}`}>{`finger${finger + 1}_motor${motor}`}</option>))}
            </select>
            <div className="h-48">
              <ResponsiveContainer width="100%" height="100%"><LineChart data={history}><CartesianGrid stroke="#334155" /><XAxis dataKey="time" hide /><YAxis width={42} /><Tooltip /><Legend /><Line type="monotone" dataKey="position" stroke="#22d3ee" dot={false} isAnimationActive={false} /><Line type="monotone" dataKey="target" stroke="#facc15" dot={false} isAnimationActive={false} /></LineChart></ResponsiveContainer>
            </div>
            <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
              {["position", "target", "goal", "speed", "load", "voltage", "temperature", "status", "moving", "estimated_current_ma"].map((metric) => (
                <div key={metric} className="rounded bg-slate-950 p-2"><p className="truncate text-slate-500">{metric}</p><p className="font-mono text-slate-200">{typeof selectedMetrics[metric] === "boolean" ? String(selectedMetrics[metric]) : pretty(selectedMetrics[metric])}</p></div>
              ))}
            </div>
          </div>

          <div className="rounded-xl border border-slate-800 bg-slate-900 p-4">
            <h2 className="mb-3 font-semibold">Unified poses</h2>
            <div className="flex gap-2"><Input value={poseName} onChange={(event) => setPoseName(event.target.value)} className="bg-slate-950" /><Button onClick={savePose}><Save className="h-4 w-4" /></Button></div>
            <div className="mt-3 max-h-44 space-y-1 overflow-auto">
              {Object.keys(poses).map((name) => <div key={name} className="flex items-center gap-1 rounded bg-slate-950 p-2 text-sm"><span className="mr-auto truncate">{name}</span><MiniButton text="Apply" onClick={() => request(`/api/superarm/poses/${encodeURIComponent(name)}/apply`, { method: "POST" })} /><button aria-label={`Delete ${name}`} onClick={async () => { await request(`/api/superarm/poses/${encodeURIComponent(name)}`, { method: "DELETE" }); refreshPrograms(); }}><Trash2 className="h-4 w-4 text-red-400" /></button></div>)}
            </div>
          </div>

          <div className="rounded-xl border border-slate-800 bg-slate-900 p-4">
            <h2 className="mb-3 font-semibold">Sequences</h2>
            <div className="flex gap-2"><Input value={sequenceName} onChange={(event) => setSequenceName(event.target.value)} className="bg-slate-950" /><Button onClick={saveSequence}><Save className="h-4 w-4" /></Button></div>
            <div className="mt-3 space-y-2">
              {sequenceSteps.map((step, index) => (
                <div key={index} className="grid grid-cols-[1fr_62px_62px_auto] gap-1 rounded bg-slate-950 p-2">
                  {"sleep_s" in step ? (
                    <><span className="rounded bg-slate-800 p-2 text-xs">Sleep</span><Input type="number" title="Sleep seconds" value={step.sleep_s} onChange={(event) => { const next = [...sequenceSteps]; next[index] = { sleep_s: Number(event.target.value) }; setSequenceSteps(next); }} className="h-8 bg-slate-800 px-1 text-xs" /><span className="p-2 text-xs text-slate-500">sec</span></>
                  ) : (
                    <><select value={step.pose || ""} onChange={(event) => { const next = [...sequenceSteps]; next[index] = { ...step, pose: event.target.value }; setSequenceSteps(next); }} className="min-w-0 rounded bg-slate-800 p-1 text-xs">{Object.keys(poses).map((name) => <option key={name}>{name}</option>)}</select><Input type="number" title="Transition seconds" value={step.transition_s || 0} onChange={(event) => { const next = [...sequenceSteps]; next[index] = { ...step, transition_s: Number(event.target.value) }; setSequenceSteps(next); }} className="h-8 bg-slate-800 px-1 text-xs" /><Input type="number" title="Hold seconds" value={step.hold_s || 0} onChange={(event) => { const next = [...sequenceSteps]; next[index] = { ...step, hold_s: Number(event.target.value) }; setSequenceSteps(next); }} className="h-8 bg-slate-800 px-1 text-xs" /></>
                  )}
                  <div className="flex"><button onClick={() => setSequenceSteps(reorderSequenceStep(sequenceSteps, index, -1))}>↑</button><button onClick={() => setSequenceSteps(reorderSequenceStep(sequenceSteps, index, 1))}>↓</button><button onClick={() => setSequenceSteps(sequenceSteps.filter((_, i) => i !== index))}>×</button></div>
                </div>
              ))}
              <div className="flex gap-2"><Button variant="outline" size="sm" onClick={() => setSequenceSteps([...sequenceSteps, { pose: Object.keys(poses)[0] || "home", transition_s: 1, hold_s: 0.5, hand_speed: globalSpeed }])}>Add pose step</Button><Button variant="outline" size="sm" onClick={() => setSequenceSteps([...sequenceSteps, { sleep_s: 1 }])}>Add sleep</Button></div>
            </div>
            <div className="mt-3 max-h-44 space-y-1 overflow-auto">
              {Object.keys(sequences).map((name) => <div key={name} className="flex items-center gap-2 rounded bg-slate-950 p-2 text-sm"><button className="mr-auto truncate" onClick={() => { setSequenceName(name); setSequenceSteps(sequences[name].steps); }}>{name}</button><button onClick={() => request(`/api/superarm/sequences/${encodeURIComponent(name)}/play`, { method: "POST", body: JSON.stringify({ loop: false }) })}><Play className="h-4 w-4 text-emerald-400" /></button><button onClick={() => request("/api/superarm/sequences/pause", { method: "POST" })}><Pause className="h-4 w-4 text-yellow-400" /></button><button onClick={() => request("/api/superarm/sequences/stop", { method: "POST" })}><CircleStop className="h-4 w-4 text-red-400" /></button></div>)}
            </div>
          </div>
        </aside>
      </main>
    </div>
  );
};

const SliderRow = ({ label, value, min, max, step = 1, onChange }: { label: string; value: number; min: number; max: number; step?: number; onChange: (value: number) => void }) => (
  <label className="grid grid-cols-[54px_1fr_42px] items-center gap-2 text-xs"><span>{label}</span><input type="range" min={min} max={max} step={step} value={value} onChange={(event) => onChange(Number(event.target.value))} /><span className="text-right font-mono text-cyan-300">{value.toFixed(step < 1 ? 2 : 0)}</span></label>
);

const MiniButton = ({ text, onClick }: { text: string; onClick: () => void }) => <button onClick={onClick} className="rounded border border-slate-700 px-2 py-1 text-xs hover:bg-slate-800">{text}</button>;

export default SuperArm;
