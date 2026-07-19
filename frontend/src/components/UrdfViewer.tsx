import React, {
  useEffect,
  useRef,
  useState,
  useMemo,
  useCallback,
  memo,
} from "react";
import { cn } from "@/lib/utils";

import URDFManipulator from "urdf-loader/src/urdf-manipulator-element.js";
import { useUrdf } from "@/hooks/useUrdf";
import { useRealTimeJoints } from "@/hooks/useRealTimeJoints";
import { useMjcfVisualLayer } from "@/hooks/useMjcfVisualLayer";
import { isAmazingHandJoint } from "@/lib/mjcfVisualLayer";
import {
  createUrdfViewer,
  setupMeshLoader,
  setupJointHighlighting,
  setupModelLoading,
  URDFViewerElement,
} from "@/lib/urdfViewerHelpers";

// Register the URDFManipulator as a custom element if it hasn't been already
if (typeof window !== "undefined" && !customElements.get("urdf-viewer")) {
  customElements.define("urdf-viewer", URDFManipulator);
}
import * as THREE from "three";

interface UrdfViewerProps {
  robotName?: string;
  showroomUrdf?: boolean;
  physicalJointNames?: string[];
}

const UrdfViewer: React.FC<UrdfViewerProps> = ({
  robotName,
  showroomUrdf = false,
  physicalJointNames = [],
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const [highlightedJoint, setHighlightedJoint] = useState<string | null>(null);
  const { registerUrdfProcessor, alternativeUrdfModels, isDefaultModel } =
    useUrdf();

  const cleanupAnimationRef = useRef<(() => void) | null>(null);
  const viewerRef = useRef<URDFViewerElement | null>(null);
  const overlayActiveRef = useRef(false);
  const hasInitializedRef = useRef<boolean>(false);
  const [robotRevision, setRobotRevision] = useState(0);
  const handJointNamesRef = useRef<string[]>([]);

  // Real-time joint updates via WebSocket
  const shouldApplyJoint = useCallback(
    (jointName: string) => !overlayActiveRef.current || !isAmazingHandJoint(jointName, handJointNamesRef.current),
    [],
  );
  const {
    isConnected: isWebSocketConnected,
    jointNames: liveJointNames,
    visualPose,
  } = useRealTimeJoints({
    viewerRef,
    enabled: true,
    shouldApplyJoint,
  });
  const useRecordModel = showroomUrdf && Boolean(robotName);
  const useSo101Model = isDefaultModel && !useRecordModel;
  const liveJointCoverage = physicalJointNames.filter((name) => liveJointNames.includes(name)).length;
  const { isActive: exactHandActive, handJointNames } = useMjcfVisualLayer({
    viewerRef,
    manifestUrl: useRecordModel
      ? `/robots/${encodeURIComponent(robotName || "")}/mujoco-visual-manifest`
      : undefined,
    visualPose,
    robotRevision,
  });
  overlayActiveRef.current = exactHandActive;
  handJointNamesRef.current = handJointNames;

  // Add state for custom URDF path
  const [customUrdfPath, setCustomUrdfPath] = useState<string | null>(null);
  const [urlModifierFunc, setUrlModifierFunc] = useState<
    ((url: string) => string) | null
  >(null);

  const packageRef = useRef<string>("");

  // Implement UrdfProcessor interface for drag and drop
  const urdfProcessor = useMemo(
    () => ({
      loadUrdf: (urdfPath: string) => {
        setCustomUrdfPath(urdfPath);
      },
      setUrlModifierFunc: (func: (url: string) => string) => {
        setUrlModifierFunc(() => func);
      },
      getPackage: () => {
        return packageRef.current;
      },
    }),
    []
  );

  // Register the URDF processor with the global drag and drop context
  useEffect(() => {
    registerUrdfProcessor(urdfProcessor);
  }, [registerUrdfProcessor, urdfProcessor]);

  // Create URL modifier function for default model
  const defaultUrlModifier = useCallback((url: string) => {
    console.log(`🔗 defaultUrlModifier called with: ${url}`);

    // Handle various package:// URL formats for the default SO-101 model
    if (url.startsWith("package://so_arm_description/meshes/")) {
      const modifiedUrl = url.replace(
        "package://so_arm_description/meshes/",
        "/so-101-urdf/meshes/"
      );
      console.log(`🔗 Modified URL (package): ${modifiedUrl}`);
      return modifiedUrl;
    }

    // Handle case where package path might be partially resolved
    if (url.includes("so_arm_description/meshes/")) {
      const modifiedUrl = url.replace(
        /.*so_arm_description\/meshes\//,
        "/so-101-urdf/meshes/"
      );
      console.log(`🔗 Modified URL (partial): ${modifiedUrl}`);
      return modifiedUrl;
    }

    // Handle the specific problematic path pattern we're seeing in logs
    if (url.includes("/so-101-urdf/so_arm_description/meshes/")) {
      const modifiedUrl = url.replace(
        "/so-101-urdf/so_arm_description/meshes/",
        "/so-101-urdf/meshes/"
      );
      console.log(`🔗 Modified URL (problematic path): ${modifiedUrl}`);
      return modifiedUrl;
    }

    // Handle relative paths that might need mesh folder prefix
    if (
      url.endsWith(".stl") &&
      !url.startsWith("/") &&
      !url.startsWith("http")
    ) {
      const modifiedUrl = `/so-101-urdf/meshes/${url}`;
      console.log(`🔗 Modified URL (relative): ${modifiedUrl}`);
      return modifiedUrl;
    }

    console.log(`🔗 Unmodified URL: ${url}`);
    return url;
  }, []);

  // Main effect to create and setup the viewer only once
  useEffect(() => {
    if (!containerRef.current) return;

    // Create and configure the URDF viewer element
    const viewer = createUrdfViewer(containerRef.current, true);
    viewerRef.current = viewer; // Store reference to the viewer

    // Determine which URDF to load - fixed path to match the actual available file
    const urdfPath = useRecordModel
      ? `/robots/${encodeURIComponent(robotName || "")}/urdf`
      : useSo101Model
      ? "/so-101-urdf/urdf/so101_new_calib.urdf"
      : customUrdfPath || "";

    // Set the package path for the default model
    if (useSo101Model) {
      packageRef.current = "/"; // Set to root so we can handle full path resolution in URL modifier
    }

    // Setup joint highlighting
    const cleanupJointHighlighting = setupJointHighlighting(
      viewer,
      setHighlightedJoint
    );

    // Function to fit the robot to the camera view
    const fitRobotToView = (viewer: URDFViewerElement) => {
      if (!viewer || !viewer.robot) {
        console.log(
          "[RobotViewer] Cannot fit to view: No viewer or robot available"
        );
        return;
      }

      try {
        // Create a bounding box for the robot
        const boundingBox = new THREE.Box3().setFromObject(viewer.robot);

        // Calculate the center of the bounding box
        const center = new THREE.Vector3();
        boundingBox.getCenter(center);

        // Calculate the size of the bounding box
        const size = new THREE.Vector3();
        boundingBox.getSize(size);

        // Get the maximum dimension to ensure the entire robot is visible
        const maxDim = Math.max(size.x, size.y, size.z);

        // Position camera to see the center of the model
        viewer.camera.position.copy(center);

        // Move the camera back to see the entire robot
        // Use the model's up direction to determine which axis to move along
        const upVector = new THREE.Vector3();
        if (viewer.up === "+Z" || viewer.up === "Z") {
          upVector.set(1, 1, 1); // Move back in a diagonal
        } else if (viewer.up === "+Y" || viewer.up === "Y") {
          upVector.set(1, 1, 1); // Move back in a diagonal
        } else {
          upVector.set(1, 1, 1); // Default direction
        }

        // Normalize the vector and multiply by the size
        upVector.normalize().multiplyScalar(maxDim * 1.3);
        viewer.camera.position.add(upVector);

        // Make the camera look at the center of the model
        viewer.controls.target.copy(center);

        // Update controls and mark for redraw
        viewer.controls.update();
        viewer.redraw();

        console.log("[RobotViewer] Robot auto-fitted to view");
      } catch (error) {
        console.error("[RobotViewer] Error fitting robot to view:", error);
      }
    };

    // Add event listener for when the robot is loaded to auto-fit to view
    const onRobotLoad = () => {
      fitRobotToView(viewer);
    };

    // URDF processing finishes before external STL files necessarily do. Fit
    // again after the final burst of mesh callbacks so a full custom robot is
    // framed instead of only the links that happened to be ready first.
    let meshFitTimer: number | undefined;
    const scheduleMeshFit = () => {
      window.clearTimeout(meshFitTimer);
      meshFitTimer = window.setTimeout(onRobotLoad, 100);
    };

    const activeUrlModifier = useSo101Model
      ? defaultUrlModifier
      : urlModifierFunc;
    setupMeshLoader(viewer, activeUrlModifier, scheduleMeshFit);

    // Setup model loading after the mesh callback is ready so even fast local
    // assets participate in the final camera fit.
    let cleanupModelLoading = () => {};
    if (urdfPath) {
      cleanupModelLoading = setupModelLoading(
        viewer,
        urdfPath,
        packageRef.current,
        setCustomUrdfPath,
        alternativeUrdfModels
      );
    }

    // Setup animation event handler for the default model or when hasAnimation is true
    const onModelProcessed = () => {
      hasInitializedRef.current = true;
      setRobotRevision((revision) => revision + 1);
      if ("setJointValue" in viewer) {
        // Clear any existing animation
        if (cleanupAnimationRef.current) {
          cleanupAnimationRef.current();
          cleanupAnimationRef.current = null;
        }
      }
      // Auto-fit the robot to view when the model is processed
      onRobotLoad();
    };

    viewer.addEventListener("urdf-processed", onModelProcessed);

    // Return cleanup function
    return () => {
      if (cleanupAnimationRef.current) {
        cleanupAnimationRef.current();
        cleanupAnimationRef.current = null;
      }
      hasInitializedRef.current = false;
      window.clearTimeout(meshFitTimer);
      cleanupJointHighlighting();
      cleanupModelLoading();
      viewer.removeEventListener("urdf-processed", onModelProcessed);
    };
  }, [
    useRecordModel,
    useSo101Model,
    robotName,
    customUrdfPath,
    urlModifierFunc,
    defaultUrlModifier,
    alternativeUrdfModels,
  ]);

  return (
    <div
      className={cn(
        "w-full h-full transition-all duration-300 ease-in-out relative",
        "bg-gradient-to-br from-gray-900 to-gray-800"
      )}
    >
      <div ref={containerRef} className="w-full h-full" />

      {/* Joint highlight indicator */}
      {highlightedJoint && (
        <div className="absolute bottom-4 right-4 bg-black/70 text-white px-3 py-2 rounded-md text-sm font-mono z-10">
          Joint: {highlightedJoint}
        </div>
      )}

      {/* WebSocket connection status */}
      {(useSo101Model || useRecordModel) && (
        <div className="absolute top-4 right-4 z-10">
          <div
            className={`flex items-center gap-2 px-3 py-2 rounded-md text-sm font-mono ${
              isWebSocketConnected
                ? "bg-green-900/70 text-green-300"
                : "bg-red-900/70 text-red-300"
            }`}
          >
            <div
              className={`w-2 h-2 rounded-full ${
                isWebSocketConnected ? "bg-green-400" : "bg-red-400"
              }`}
            />
            {isWebSocketConnected ? "Live Robot Data" : "Disconnected"}
          </div>
          {useRecordModel && (
            <div className={`mt-2 rounded-md px-3 py-2 text-xs font-mono ${
              liveJointCoverage === physicalJointNames.length && physicalJointNames.length > 0
                ? "bg-green-900/70 text-green-300"
                : "bg-amber-900/70 text-amber-200"
            }`}>
              <div>{robotName}</div>
              <div>{liveJointCoverage}/{physicalJointNames.length} physical joints live</div>
              {physicalJointNames.length > 0 && liveJointCoverage !== physicalJointNames.length && (
                <div className="mt-1">Runtime/URDF joint mismatch</div>
              )}
              <div className="mt-1">{exactHandActive ? "Exact MuJoCo hand visuals" : "URDF hand fallback"}</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default memo(UrdfViewer);
