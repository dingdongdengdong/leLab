import { RefObject, useEffect, useRef, useState } from "react";

import {
  MjcfVisualLayer,
  MjcfVisualPose,
  normalizeVisualManifest,
  VisualPoseBuffer,
} from "@/lib/mjcfVisualLayer";
import { URDFViewerElement } from "@/lib/urdfViewerHelpers";

interface UseMjcfVisualLayerProps {
  viewerRef: RefObject<URDFViewerElement>;
  manifestUrl?: string;
  visualPose?: MjcfVisualPose | null;
  robotRevision: number;
}

export const useMjcfVisualLayer = ({
  viewerRef,
  manifestUrl,
  visualPose,
  robotRevision,
}: UseMjcfVisualLayerProps) => {
  const layerRef = useRef<MjcfVisualLayer | null>(null);
  const bufferRef = useRef(new VisualPoseBuffer());
  const pendingPoseRef = useRef<MjcfVisualPose | null>(null);
  const [isActive, setIsActive] = useState(false);
  const [handJointNames, setHandJointNames] = useState<string[]>([]);

  useEffect(() => {
    if (!visualPose) return;
    pendingPoseRef.current = visualPose;
    bufferRef.current.push(visualPose);
  }, [visualPose]);

  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || !manifestUrl || robotRevision === 0) return;
    let cancelled = false;
    const controller = new AbortController();
    const baseUrl = new URL(manifestUrl, window.location.origin);
    const resolveUrl = (url: string) => new URL(url, baseUrl).toString();

    fetch(manifestUrl, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(`AmazingHand manifest unavailable (${response.status})`);
        const manifest = normalizeVisualManifest(await response.json());
        if (!manifest) throw new Error("AmazingHand manifest is invalid");
        const layer = await MjcfVisualLayer.create(viewer, manifest, resolveUrl);
        if (cancelled) {
          layer.dispose();
          return;
        }
        layerRef.current?.dispose();
        layerRef.current = layer;
        setHandJointNames(manifest.hand_joint_names);
        setIsActive(true);
        if (pendingPoseRef.current) layer.applyPose(pendingPoseRef.current);
      })
      .catch((error) => {
        if (!controller.signal.aborted) console.warn("Exact AmazingHand visuals unavailable:", error);
      });

    return () => {
      cancelled = true;
      controller.abort();
      layerRef.current?.dispose();
      layerRef.current = null;
      setIsActive(false);
      setHandJointNames([]);
    };
  }, [manifestUrl, robotRevision, viewerRef]);

  useEffect(() => {
    if (!isActive) return;
    let animationFrame = 0;
    const render = (now: number) => {
      const pose = bufferRef.current.sample(now);
      if (pose) layerRef.current?.applyPose(pose);
      animationFrame = requestAnimationFrame(render);
    };
    animationFrame = requestAnimationFrame(render);
    return () => cancelAnimationFrame(animationFrame);
  }, [isActive]);

  return { isActive, handJointNames };
};
