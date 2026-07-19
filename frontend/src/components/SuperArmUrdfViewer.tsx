import { useCallback, useEffect, useRef, useState } from "react";
import * as THREE from "three";
import URDFManipulator from "urdf-loader/src/urdf-manipulator-element.js";

import { useApi } from "@/contexts/ApiContext";
import {
  createUrdfViewer,
  setupMeshLoader,
  setupModelLoading,
  URDFViewerElement,
} from "@/lib/urdfViewerHelpers";

if (typeof window !== "undefined" && !customElements.get("urdf-viewer")) {
  customElements.define("urdf-viewer", URDFManipulator);
}

interface SuperArmUrdfViewerProps {
  jointPositions: Record<string, number>;
}

const fitRobotToView = (viewer: URDFViewerElement) => {
  if (!viewer.robot) return;
  const bounds = new THREE.Box3().setFromObject(viewer.robot);
  const center = bounds.getCenter(new THREE.Vector3());
  const size = bounds.getSize(new THREE.Vector3());
  const extent = Math.max(size.x, size.y, size.z, 0.1);
  viewer.controls.target.copy(center);
  const cameraOffset = new THREE.Vector3(1, 1, 0.65)
    .normalize()
    .multiplyScalar(extent * 1.05);
  viewer.camera.position.copy(center).add(cameraOffset);
  viewer.camera.near = extent / 100;
  viewer.camera.far = extent * 100;
  viewer.camera.lookAt(center);
  viewer.camera.updateProjectionMatrix();
  viewer.controls.update();
  viewer.redraw();
};

const SuperArmUrdfViewer = ({ jointPositions }: SuperArmUrdfViewerProps) => {
  const { baseUrl } = useApi();
  const containerRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<URDFViewerElement | null>(null);
  const [status, setStatus] = useState("Loading source-arm URDF…");

  const resolveApiUrl = useCallback(
    (path: string) => {
      const assetPath = "/api/superarm/urdf/meshes/";
      const assetIndex = path.indexOf(assetPath);
      return assetIndex >= 0 ? `${baseUrl}${path.slice(assetIndex)}` : path;
    },
    [baseUrl],
  );

  useEffect(() => {
    if (!containerRef.current) return;
    const viewer = createUrdfViewer(containerRef.current, true);
    viewerRef.current = viewer;
    setupMeshLoader(viewer, resolveApiUrl);

    const onProcessed = () => {
      fitRobotToView(viewer);
      setStatus("LeLab URDF reference · live joint mirror");
    };
    const onError = () => setStatus("URDF reference unavailable");
    viewer.addEventListener("urdf-processed", onProcessed);
    viewer.addEventListener("error", onError);
    const cleanupLoading = setupModelLoading(
      viewer,
      `${baseUrl}/api/superarm/urdf`,
      "",
      () => undefined,
      [],
    );

    return () => {
      cleanupLoading();
      viewer.removeEventListener("urdf-processed", onProcessed);
      viewer.removeEventListener("error", onError);
      viewer.remove();
      viewerRef.current = null;
    };
  }, [baseUrl, resolveApiUrl]);

  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer) return;
    Object.entries(jointPositions).forEach(([joint, value]) => {
      viewer.setJointValue(joint, value);
    });
    viewer.redraw();
  }, [jointPositions]);

  return (
    <div className="relative h-full min-h-[360px] overflow-hidden bg-black">
      <div ref={containerRef} className="absolute inset-0" />
      <span className="absolute bottom-2 left-2 rounded bg-black/75 px-2 py-1 text-xs text-slate-200">
        {status}
      </span>
    </div>
  );
};

export default SuperArmUrdfViewer;
