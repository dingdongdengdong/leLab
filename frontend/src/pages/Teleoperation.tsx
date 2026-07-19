import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import VisualizerPanel from "@/components/control/VisualizerPanel";
import TeleopCameraPanel from "@/components/control/TeleopCameraPanel";
import { useToast } from "@/hooks/use-toast";
import { useApi } from "@/contexts/ApiContext";

const TeleoperationPage = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { toast } = useToast();
  const { baseUrl, fetchWithHeaders } = useApi();
  const routeState = location.state as {
    robot_name?: string;
    showroom_urdf?: boolean;
    physical_joint_names?: string[];
  } | null;
  const robotName = useMemo(
    () => routeState?.robot_name || new URLSearchParams(location.search).get("robot") || undefined,
    [location.search, routeState?.robot_name]
  );
  const [showroomUrdf, setShowroomUrdf] = useState(Boolean(routeState?.showroom_urdf));
  const [physicalJointNames, setPhysicalJointNames] = useState<string[]>(
    routeState?.physical_joint_names || []
  );

  useEffect(() => {
    if (!robotName || routeState?.showroom_urdf !== undefined) return;
    let cancelled = false;
    fetchWithHeaders(`${baseUrl}/robots/${encodeURIComponent(robotName)}`)
      .then((response) => response.json())
      .then((data) => {
        if (cancelled || !data?.robot) return;
        setShowroomUrdf(Boolean(data.robot.urdf_path));
        setPhysicalJointNames(data.robot.physical_joint_names || []);
      })
      .catch(() => {
        if (!cancelled) setShowroomUrdf(false);
      });
    return () => {
      cancelled = true;
    };
  }, [baseUrl, fetchWithHeaders, robotName, routeState?.showroom_urdf]);

  // Stop teleoperation exactly once, however the user leaves, so the back
  // button, an in-app link, and the unmount safety net can't double-stop or
  // double-toast.
  const stoppedRef = useRef(false);
  const stopTeleoperation = useCallback(async () => {
    if (stoppedRef.current) return;
    stoppedRef.current = true;
    try {
      const res = await fetchWithHeaders(`${baseUrl}/stop-teleoperation`, {
        method: "POST",
      });
      const data = await res.json();
      if (data?.success) {
        toast({
          title: "Teleoperation stopped",
          description: "The arm was disconnected cleanly.",
        });
      }
    } catch {
      /* best-effort */
    }
  }, [baseUrl, fetchWithHeaders, toast]);

  // Cover every exit path so a session can't keep running and block the next
  // start with "already active":
  //   - the back button awaits stopTeleoperation() then navigates (below);
  //   - any other in-app navigation unmounts this component → stop via cleanup;
  //   - a browser-level leave (URL change, reload, tab close) never runs React
  //     cleanup, so `pagehide` fires a keepalive stop that survives the unload
  //     and stashes a flag the next page reads to confirm the clean disconnect.
  //     It uses a bare fetch (no JSON Content-Type) so the request stays a CORS
  //     "simple request" and isn't dropped to a preflight mid-unload.
  useEffect(() => {
    const handlePageHide = () => {
      try {
        sessionStorage.setItem("lelab:teleop-stopped", "1");
      } catch {
        /* sessionStorage may be unavailable; the stop below still runs */
      }
      fetch(`${baseUrl}/stop-teleoperation`, {
        method: "POST",
        keepalive: true,
      }).catch(() => {});
    };
    window.addEventListener("pagehide", handlePageHide);

    return () => {
      window.removeEventListener("pagehide", handlePageHide);
      stopTeleoperation();
    };
  }, [baseUrl, stopTeleoperation]);

  const handleGoBack = async () => {
    await stopTeleoperation();
    navigate("/");
  };

  return (
    <div className="min-h-screen bg-black flex items-center justify-center p-2 sm:p-4">
      <div className="w-full h-[95vh] flex">
        <VisualizerPanel
          onGoBack={handleGoBack}
          className="lg:w-full"
          robotName={robotName}
          showroomUrdf={showroomUrdf}
          physicalJointNames={physicalJointNames}
          rightSlot={<TeleopCameraPanel />}
        />
      </div>
    </div>
  );
};

export default TeleoperationPage;
