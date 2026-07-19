import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ArrowLeft, Gamepad2, Send, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useApi } from "@/contexts/ApiContext";
import { useToast } from "@/hooks/use-toast";

interface ManualLeaderSlider {
  name: string;
  label: string;
  min: number;
  max: number;
  step: number;
  default: number;
}

interface ManualLeaderPreset {
  name: string;
  action: number[];
}

interface HandMotion {
  name: string;
  label: string;
  code: number;
  joint_targets: Record<string, number>;
}

interface ActionResult {
  resolved_logical_action?: number[] | Record<string, number>;
  physical_targets?: Record<string, number>;
  joint_positions?: Record<string, number>;
}

interface ManualLeaderConfig {
  status: string;
  robot_name: string;
  robot_backend: string;
  joint_names: string[];
  physical_joint_names: string[];
  sliders: ManualLeaderSlider[];
  hand_motions: HandMotion[];
  presets: ManualLeaderPreset[];
  start_endpoint: string;
  action_endpoint: string;
  stop_endpoint: string;
  start_request: Record<string, unknown>;
}

const DEFAULT_ROBOT = "SuperArm + AmazingHand";

const formatValue = (value: number): string => value.toFixed(2);

const ManualLeaderPage: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { baseUrl, fetchWithHeaders } = useApi();
  const { toast } = useToast();
  const [config, setConfig] = useState<ManualLeaderConfig | null>(null);
  const [values, setValues] = useState<number[]>([]);
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [lastSent, setLastSent] = useState<number[] | null>(null);
  const [lastResult, setLastResult] = useState<ActionResult | null>(null);
  const connectedRef = useRef(false);
  const configRef = useRef<ManualLeaderConfig | null>(null);

  const robotName = useMemo(() => {
    const stateRobot = (location.state as { robot_name?: string } | null)?.robot_name;
    const queryRobot = new URLSearchParams(location.search).get("robot");
    return stateRobot || queryRobot || DEFAULT_ROBOT;
  }, [location.search, location.state]);

  useEffect(() => {
    let cancelled = false;
    const loadConfig = async () => {
      setLoading(true);
      try {
        const res = await fetchWithHeaders(
          `${baseUrl}/manual-leader-config/${encodeURIComponent(robotName)}`
        );
        const data = await res.json();
        if (!res.ok || data.status !== "success") {
          throw new Error(data.message || "Manual web leader is not available for this robot.");
        }
        if (cancelled) return;
        setConfig(data);
        configRef.current = data;
        const armDefaults = data.sliders.map((slider: ManualLeaderSlider) => slider.default);
        const defaultMotion = data.hand_motions?.[0]?.code;
        setValues(defaultMotion === undefined ? armDefaults : [...armDefaults, defaultMotion]);
      } catch (error) {
        if (cancelled) return;
        toast({
          title: "Manual Leader Unavailable",
          description: error instanceof Error ? error.message : "Could not load slider config.",
          variant: "destructive",
        });
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    loadConfig();
    return () => {
      cancelled = true;
    };
  }, [baseUrl, fetchWithHeaders, robotName, toast]);

  const stopSession = useCallback(async (showToast = true) => {
    const currentConfig = configRef.current;
    if (!connectedRef.current || !currentConfig) return;
    connectedRef.current = false;
    setConnected(false);
    try {
      const res = await fetchWithHeaders(`${baseUrl}${currentConfig.stop_endpoint}`, {
        method: "POST",
      });
      const data = await res.json();
      if (showToast && data?.success) {
        toast({ title: "Manual leader stopped", description: "Follower session closed." });
      }
    } catch {
      /* best effort */
    }
  }, [baseUrl, fetchWithHeaders, toast]);

  useEffect(() => {
    const handlePageHide = () => {
      const currentConfig = configRef.current;
      if (!connectedRef.current || !currentConfig) return;
      connectedRef.current = false;
      fetch(`${baseUrl}${currentConfig.stop_endpoint}`, {
        method: "POST",
        keepalive: true,
      }).catch(() => {});
    };
    window.addEventListener("pagehide", handlePageHide);
    return () => {
      window.removeEventListener("pagehide", handlePageHide);
      stopSession(false);
    };
  }, [baseUrl, stopSession]);

  const connect = async () => {
    if (!config) return;
    setBusy(true);
    try {
      const res = await fetchWithHeaders(`${baseUrl}${config.start_endpoint}`, {
        method: "POST",
        body: JSON.stringify(config.start_request),
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.message || "Could not connect the Isaac Sim follower backend.");
      }
      connectedRef.current = true;
      setConnected(true);
      toast({
        title: "Manual leader connected",
        description: "Use sliders or presets, then Send Slider Action.",
      });
    } catch (error) {
      toast({
        title: "Connect Failed",
        description: error instanceof Error ? error.message : "Could not connect.",
        variant: "destructive",
      });
    } finally {
      setBusy(false);
    }
  };

  const sendAction = async (action = values) => {
    if (!config) return;
    setBusy(true);
    try {
      const res = await fetchWithHeaders(`${baseUrl}${config.action_endpoint}`, {
        method: "POST",
        body: JSON.stringify({ action }),
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.message || "Could not send slider action.");
      }
      setValues(action);
      setLastSent(action);
      setLastResult(data);
      toast({
        title: "Slider action sent",
        description: action.map(formatValue).join(", "),
      });
    } catch (error) {
      toast({
        title: "Send Failed",
        description: error instanceof Error ? error.message : "Could not send action.",
        variant: "destructive",
      });
    } finally {
      setBusy(false);
    }
  };

  const updateValue = (index: number, value: number) => {
    setValues((current) => current.map((v, i) => (i === index ? value : v)));
  };

  const selectHandMotion = (motion: HandMotion) => {
    const armValues = values.slice(0, config?.sliders.length ?? 0);
    void sendAction([...armValues, motion.code]);
  };

  const goBack = async () => {
    await stopSession(false);
    navigate("/");
  };

  return (
    <div className="min-h-screen bg-black text-white p-4 sm:p-6">
      <div className="mx-auto flex max-w-5xl flex-col gap-4">
        <div className="flex items-center justify-between gap-3">
          <Button variant="outline" className="border-gray-700 bg-gray-900 text-white" onClick={goBack}>
            <ArrowLeft className="mr-2 h-4 w-4" /> Back to LeLab
          </Button>
          <Badge variant={connected ? "default" : "outline"} className="border-gray-600 text-white">
            {connected ? "Follower connected" : "Follower idle"}
          </Badge>
        </div>

        <Card className="border-gray-800 bg-gray-900 text-white">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Gamepad2 className="h-6 w-6 text-yellow-400" /> Manual Web Leader
            </CardTitle>
            <CardDescription className="text-gray-400">
              Browser sliders for testing the custom Isaac Sim follower without a physical SO101 leader arm.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {loading && <p className="text-sm text-gray-400">Loading slider config...</p>}
            {!loading && !config && (
              <p className="text-sm text-red-300">No manual leader config is available for {robotName}.</p>
            )}
            {config && (
              <>
                <div className="grid gap-2 text-sm text-gray-300 sm:grid-cols-3">
                  <div>Robot: <span className="text-white">{config.robot_name}</span></div>
                  <div>Backend: <span className="text-white">{config.robot_backend}</span></div>
                  <div>Policy controls: <span className="text-white">{config.joint_names.length}</span></div>
                </div>

                {config.hand_motions.length > 0 && (
                  <div className="space-y-2 rounded-lg border border-cyan-800/60 bg-cyan-950/20 p-3">
                    <div>
                      <h3 className="text-sm font-semibold text-cyan-200">AmazingHand fixed motion</h3>
                      <p className="text-xs text-gray-400">
                        One logical control selects a complete eight-joint hand pose.
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {config.hand_motions.map((motion) => (
                        <Button
                          key={motion.name}
                          variant="outline"
                          disabled={!connected || busy}
                          className="border-cyan-700 bg-gray-950 text-white"
                          onClick={() => selectHandMotion(motion)}
                        >
                          {motion.label} ({formatValue(motion.code)})
                        </Button>
                      ))}
                    </div>
                  </div>
                )}

                <div className="flex flex-wrap gap-2">
                  <Button onClick={connect} disabled={connected || busy} className="bg-yellow-500 text-white hover:bg-yellow-600">
                    Connect Isaac Follower
                  </Button>
                  <Button onClick={() => stopSession()} disabled={!connected || busy} variant="outline" className="border-gray-700 bg-gray-950 text-white">
                    <Square className="mr-2 h-4 w-4" /> Stop
                  </Button>
                  <Button onClick={() => sendAction(values)} disabled={!connected || busy} className="bg-green-600 text-white hover:bg-green-700">
                    <Send className="mr-2 h-4 w-4" /> Send Slider Action
                  </Button>
                </div>

                <div className="grid gap-3">
                  {config.sliders.map((slider, index) => (
                    <div key={slider.name} className="rounded-lg border border-gray-800 bg-black/40 p-3">
                      <div className="mb-2 flex items-center justify-between gap-3 text-sm">
                        <span className="font-medium text-gray-200">{slider.label}</span>
                        <span className="font-mono text-yellow-300">{formatValue(values[index] ?? slider.default)} rad</span>
                      </div>
                      <input
                        aria-label={slider.label}
                        type="range"
                        min={slider.min}
                        max={slider.max}
                        step={slider.step}
                        value={values[index] ?? slider.default}
                        onChange={(event) => updateValue(index, Number(event.target.value))}
                        className="w-full accent-yellow-500"
                      />
                      <div className="mt-1 flex justify-between text-xs text-gray-500">
                        <span>{formatValue(slider.min)}</span>
                        <span>{formatValue(slider.max)}</span>
                      </div>
                    </div>
                  ))}
                </div>

                <div className="space-y-2">
                  <h3 className="text-sm font-semibold text-gray-200">Presets</h3>
                  <div className="flex flex-wrap gap-2">
                    {config.presets.map((preset) => (
                      <Button
                        key={preset.name}
                        variant="outline"
                        disabled={!connected || busy}
                        className="border-gray-700 bg-gray-950 text-white"
                        onClick={() => sendAction(preset.action)}
                      >
                        {preset.name}
                      </Button>
                    ))}
                  </div>
                </div>

                {lastSent && (
                  <div className="space-y-1 rounded-lg border border-gray-800 bg-black/40 p-3 text-xs text-gray-400">
                    <p>
                      Sent 6D action: <span className="font-mono text-gray-200">[{lastSent.map(formatValue).join(", ")}]</span>
                    </p>
                    {lastResult?.resolved_logical_action && (
                      <p className="break-all">
                        Resolved logical: <span className="font-mono text-green-300">{JSON.stringify(lastResult.resolved_logical_action)}</span>
                      </p>
                    )}
                    {lastResult?.physical_targets && (
                      <p className="break-all">
                        Expanded physical target: <span className="font-mono text-cyan-300">{JSON.stringify(lastResult.physical_targets)}</span>
                      </p>
                    )}
                    {lastResult?.joint_positions && (
                      <p className="break-all">
                        Follower readback: <span className="font-mono text-yellow-300">{JSON.stringify(lastResult.joint_positions)}</span>
                      </p>
                    )}
                  </div>
                )}
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
};

export default ManualLeaderPage;
