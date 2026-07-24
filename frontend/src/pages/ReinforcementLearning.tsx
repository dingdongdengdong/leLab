import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Play, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useApi } from "@/contexts/ApiContext";
import {
  getJob, getJobLogs, getRlReadiness, JobRecord, LogLine,
  ReinforcementLearningRequest, reinforcementLearningFrameUrl,
  startReinforcementLearningJob, stopJob,
} from "@/lib/jobsApi";

const SHA = "3bd316090d17f9903562139983a6c66731717f7246045ebdaf90610bf3e596d3";
const DEFAULT_ZIP = "/home/dong/july/superarm_ws.omx-artifacts/lelab-isaacsim-control/distributions/superarm_amazinghand_isaac_sim_usd_distribution_20260722_v2.zip";
const initial: ReinforcementLearningRequest = {
  task: "SuperArmIsaacPickLift-v0", runner: "local", seed: 1000,
  episode_length_steps: 150, training_steps: 20000,
  online_buffer_capacity: 100000, learning_starts: 100, batch_size: 256,
  actor_lr: 0.0003, critic_lr: 0.0003, temperature_lr: 0.0003,
  checkpoint_frequency: 5000, distribution_zip: DEFAULT_ZIP,
  distribution_sha256: SHA, learner_port: 50051, bridge_port: 8765,
  camera_preview: true,
};

export default function ReinforcementLearning() {
  const { baseUrl, fetchWithHeaders } = useApi();
  const navigate = useNavigate();
  const { jobId } = useParams();
  const [config, setConfig] = useState(initial);
  const [job, setJob] = useState<JobRecord | null>(null);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [ready, setReady] = useState(false);
  const [checks, setChecks] = useState<Record<string, boolean>>({});
  const [frameNonce, setFrameNonce] = useState(Date.now());
  const setNumber = (key: keyof ReinforcementLearningRequest, value: string) =>
    setConfig((old) => ({ ...old, [key]: Number(value) }));

  useEffect(() => {
    if (!jobId) {
      getRlReadiness(baseUrl, fetchWithHeaders, config).then((r) => { setReady(r.ready); setChecks(r.checks); }).catch(() => setReady(false));
      return;
    }
    const poll = () => {
      getJob(baseUrl, fetchWithHeaders, jobId).then(setJob);
      getJobLogs(baseUrl, fetchWithHeaders, jobId).then((next) => setLogs((old) => [...old, ...next].slice(-1000)));
      setFrameNonce(Date.now());
    };
    poll();
    const timer = window.setInterval(poll, 500);
    return () => window.clearInterval(timer);
  // Readiness is refreshed when entering configuration mode; starting performs
  // the same authoritative server-side check again.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseUrl, fetchWithHeaders, jobId]);

  const fields: [keyof ReinforcementLearningRequest, string][] = [
    ["seed", "Seed"], ["episode_length_steps", "Episode length"], ["training_steps", "Training steps"],
    ["online_buffer_capacity", "Replay capacity"], ["learning_starts", "Replay warmup"], ["batch_size", "Batch size"],
    ["actor_lr", "Actor LR"], ["critic_lr", "Critic LR"], ["temperature_lr", "Temperature LR"],
    ["checkpoint_frequency", "Checkpoint frequency"],
  ];
  const m = job?.metrics;
  return <main className="min-h-screen bg-slate-950 p-6 text-slate-100">
    <div className="mx-auto max-w-7xl space-y-6">
      <Button variant="ghost" onClick={() => navigate("/")}><ArrowLeft className="mr-2 h-4 w-4" />Home</Button>
      <header><p className="text-sm font-semibold text-yellow-400">AUTONOMOUS SAC · LOCAL GPU</p><h1 className="text-3xl font-bold">Reinforcement Learning — SuperArm Isaac Sim</h1><p className="text-slate-400">SuperArm + AmazingHand learns seeded cube pick-and-lift from 256×256 RGB and 23-value state.</p></header>
      {!jobId ? <div className="grid gap-6 lg:grid-cols-2">
        <section className="rounded-xl border border-slate-800 bg-slate-900 p-5 space-y-4">
          <h2 className="text-xl font-semibold">Configuration</h2>
          <label className="block text-sm">Task<Input value={config.task} disabled /></label>
          <label className="block text-sm">Isaac distribution<Input value={config.distribution_zip} onChange={(e) => setConfig({...config, distribution_zip:e.target.value})} /></label>
          <label className="block text-sm">Validated checksum<Input value={config.distribution_sha256} onChange={(e) => setConfig({...config, distribution_sha256:e.target.value})} /></label>
          <div className="grid grid-cols-2 gap-3">{fields.map(([key,label]) => <label key={key} className="text-sm">{label}<Input type="number" value={String(config[key])} onChange={(e) => setNumber(key,e.target.value)} /></label>)}</div>
          <Button disabled={!ready} className="w-full bg-yellow-400 text-slate-950 hover:bg-yellow-300" onClick={async () => { const created=await startReinforcementLearningJob(baseUrl,fetchWithHeaders,config); navigate(`/reinforcement-learning/${created.id}`); }}><Play className="mr-2 h-4 w-4" />Start autonomous SAC</Button>
        </section>
        <section className="rounded-xl border border-slate-800 bg-slate-900 p-5"><h2 className="text-xl font-semibold">Readiness</h2><div className="mt-4 space-y-2">{Object.entries(checks).map(([name,ok])=><div key={name} className="flex justify-between"><span>{name.replaceAll("_"," ")}</span><span className={ok?"text-emerald-400":"text-red-400"}>{ok?"Ready":"Blocked"}</span></div>)}</div></section>
      </div> : <div className="grid gap-6 lg:grid-cols-2">
        <section className="rounded-xl border border-slate-800 bg-slate-900 p-5 space-y-4"><div className="flex justify-between"><h2 className="text-xl font-semibold">{job?.name ?? "Loading…"}</h2>{job?.state==="running"&&<Button variant="destructive" onClick={async()=>setJob(await stopJob(baseUrl,fetchWithHeaders,jobId))}><Square className="mr-2 h-4 w-4"/>Stop</Button>}</div><img className="aspect-square w-full rounded bg-black object-contain" src={reinforcementLearningFrameUrl(baseUrl,jobId,frameNonce)} alt="Policy workspace camera" /><div className="grid grid-cols-3 gap-2 text-sm">{[["Actor",m?.actor_status],["Learner",m?.learner_status],["Isaac",m?.isaac_status],["Episode",m?.episode],["Return",m?.episode_return],["Success",m?.success_rate],["Replay",m?.replay_size],["Actor loss",m?.actor_loss],["Critic loss",m?.critic_loss],["Temperature",m?.temperature],["FPS",m?.actor_fps],["Checkpoints",job?.checkpoint_count]].map(([k,v])=><div key={String(k)} className="rounded bg-slate-800 p-3"><div className="text-slate-400">{k}</div><div>{v ?? "—"}</div></div>)}</div></section>
        <section className="rounded-xl border border-slate-800 bg-slate-900 p-5"><h2 className="text-xl font-semibold">Combined logs</h2><pre className="mt-4 h-[720px] overflow-auto whitespace-pre-wrap text-xs text-slate-300">{logs.map((l)=>l.message).join("\n")}</pre></section>
      </div>}
    </div>
  </main>;
}
