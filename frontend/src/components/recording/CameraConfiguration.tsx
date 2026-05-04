import React, { useState, useEffect, useRef, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Camera, Plus, X, VideoOff } from "lucide-react";
import { useApi } from "@/contexts/ApiContext";
import { useToast } from "@/hooks/use-toast";

export interface CameraConfig {
  id: string;
  name: string;
  type: string;
  camera_index?: number; // cv2 index — what the recorder opens
  device_id: string; // Browser deviceId matched to the cv2 index by AVFoundation localizedName
  width: number;
  height: number;
  fps?: number;
}

interface CameraConfigurationProps {
  cameras: CameraConfig[];
  onCamerasChange: (cameras: CameraConfig[]) => void;
  releaseStreamsRef?: React.MutableRefObject<(() => void) | null>; // Ref to expose stream release function
}

interface AvailableCamera {
  index: number;
  deviceId: string;
  name: string;
  available: boolean;
}

const CameraConfiguration: React.FC<CameraConfigurationProps> = ({
  cameras,
  onCamerasChange,
  releaseStreamsRef,
}) => {
  const { baseUrl, fetchWithHeaders } = useApi();
  const { toast } = useToast();

  const [availableCameras, setAvailableCameras] = useState<AvailableCamera[]>(
    []
  );
  const [selectedCameraIndex, setSelectedCameraIndex] = useState<string>("");
  const [cameraName, setCameraName] = useState("");
  const [isLoadingCameras, setIsLoadingCameras] = useState(false);

  // Fetch available cameras on component mount
  useEffect(() => {
    fetchAvailableCameras();
  }, []);

  const fetchAvailableCameras = async () => {
    setIsLoadingCameras(true);
    try {
      // Need a permission grant before enumerateDevices() returns labels.
      try {
        const probe = await navigator.mediaDevices.getUserMedia({ video: true });
        probe.getTracks().forEach((t) => t.stop());
      } catch (permError) {
        console.warn("Camera permission denied; labels will be empty", permError);
      }

      const browserDevices = (await navigator.mediaDevices.enumerateDevices())
        .filter((d) => d.kind === "videoinput")
        .map((d) => ({ deviceId: d.deviceId, label: d.label }));

      const response = await fetchWithHeaders(`${baseUrl}/available-cameras`);
      if (!response.ok) {
        setAvailableCameras([]);
        return;
      }
      const data = await response.json();
      const backendCams = (data.cameras || []) as {
        index: number;
        name?: string;
        available: boolean;
      }[];

      // Match each cv2 index to a browser deviceId by AVFoundation
      // localizedName (== MediaDeviceInfo.label on macOS, verbatim).
      const usedBrowserIds = new Set<string>();
      const merged: AvailableCamera[] = backendCams.map((cam) => {
        const label = cam.name || `Camera ${cam.index}`;
        const matched = browserDevices.find(
          (d) => d.label === label && !usedBrowserIds.has(d.deviceId)
        );
        if (matched) usedBrowserIds.add(matched.deviceId);
        return {
          index: cam.index,
          deviceId: matched?.deviceId || "",
          name: label,
          available: cam.available,
        };
      });
      setAvailableCameras(merged);
    } catch (error) {
      console.error("Camera enumeration failed:", error);
      toast({
        title: "Camera Detection Failed",
        description: "Could not detect available cameras.",
        variant: "destructive",
      });
    } finally {
      setIsLoadingCameras(false);
    }
  };

  const addCamera = () => {
    if (!selectedCameraIndex || !cameraName.trim()) {
      toast({
        title: "Missing Information",
        description: "Please select a camera and provide a name.",
        variant: "destructive",
      });
      return;
    }

    const cameraIndex = parseInt(selectedCameraIndex);
    const selectedCamera = availableCameras.find(
      (cam) => cam.index === cameraIndex
    );

    if (!selectedCamera) {
      toast({
        title: "Invalid Camera",
        description: "Selected camera is not available.",
        variant: "destructive",
      });
      return;
    }

    // Check if camera is already added
    if (cameras.some((cam) => cam.camera_index === cameraIndex)) {
      toast({
        title: "Camera Already Added",
        description: "This camera is already in the configuration.",
        variant: "destructive",
      });
      return;
    }

    const newCamera: CameraConfig = {
      id: `camera_${Date.now()}`,
      name: cameraName.trim(),
      type: "opencv",
      camera_index: selectedCamera.index,
      device_id: selectedCamera.deviceId,
      width: 640,
      height: 480,
      fps: 30,
    };

    onCamerasChange([...cameras, newCamera]);

    setSelectedCameraIndex("");
    setCameraName("");

    toast({
      title: "Camera Added",
      description: `${newCamera.name} has been added to the configuration.`,
    });
  };

  const removeCamera = (cameraId: string) => {
    onCamerasChange(cameras.filter((cam) => cam.id !== cameraId));
    toast({
      title: "Camera Removed",
      description: "Camera has been removed from the configuration.",
    });
  };

  const updateCamera = (cameraId: string, updates: Partial<CameraConfig>) => {
    onCamerasChange(
      cameras.map((cam) =>
        cam.id === cameraId ? { ...cam, ...updates } : cam
      )
    );
  };

  // When the recording session is starting, the parent calls
  // releaseStreamsRef.current() to make every CameraPreview drop its browser
  // stream so cv2.VideoCapture can grab the camera exclusively.
  const [streamsPaused, setStreamsPaused] = useState(false);
  const releaseAllCameraStreams = useCallback(() => {
    setStreamsPaused(true);
  }, []);

  useEffect(() => {
    if (releaseStreamsRef) {
      releaseStreamsRef.current = releaseAllCameraStreams;
    }
  }, [releaseStreamsRef, releaseAllCameraStreams]);


  return (
    <div className="space-y-4">
      <h3 className="text-lg font-semibold text-white border-b border-gray-700 pb-2">
        Camera Configuration
      </h3>

      {/* Add Camera Section */}
      <div className="bg-gray-800/50 rounded-lg p-4 space-y-4">
        <h4 className="text-md font-medium text-gray-300">Add Camera</h4>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div className="space-y-2">
            <Label className="text-sm font-medium text-gray-300">
              Available Cameras
            </Label>
            <Select
              value={selectedCameraIndex}
              onValueChange={setSelectedCameraIndex}
              disabled={isLoadingCameras}
            >
              <SelectTrigger className="bg-gray-800 border-gray-700 text-white">
                <SelectValue
                  placeholder={
                    isLoadingCameras ? "Loading cameras..." : "Select camera"
                  }
                />
              </SelectTrigger>
              <SelectContent className="bg-gray-800 border-gray-700">
                {availableCameras.map((camera) => {
                  const alreadyAdded = cameras.some(
                    (cam) => cam.camera_index === camera.index
                  );
                  return (
                    <SelectItem
                      key={camera.index}
                      value={camera.index.toString()}
                      className="text-white hover:bg-gray-700"
                      disabled={!camera.available || alreadyAdded}
                    >
                      <div className="flex flex-col">
                        <span className="font-medium">{camera.name}</span>
                        <span className="text-xs text-gray-400">
                          Index {camera.index}
                          {alreadyAdded && " · already added"}
                        </span>
                      </div>
                    </SelectItem>
                  );
                })}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label className="text-sm font-medium text-gray-300">
              Camera Name
            </Label>
            <Input
              value={cameraName}
              onChange={(e) => setCameraName(e.target.value)}
              placeholder="e.g., workspace_cam"
              className="bg-gray-800 border-gray-700 text-white"
            />
          </div>

          <div className="space-y-2 flex flex-col justify-end">
            <Button
              onClick={addCamera}
              className="bg-blue-500 hover:bg-blue-600 text-white"
              disabled={!selectedCameraIndex || !cameraName.trim()}
            >
              <Plus className="w-4 h-4 mr-2" />
              Add Camera
            </Button>
          </div>
        </div>
      </div>

      {/* Configured Cameras */}
      {cameras.length > 0 && (
        <div className="space-y-4">
          <h4 className="text-md font-medium text-gray-300">
            Configured Cameras ({cameras.length})
          </h4>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-2 gap-4">
            {cameras.map((camera) => (
              <CameraPreview
                key={camera.id}
                camera={camera}
                paused={streamsPaused}
                onRemove={() => removeCamera(camera.id)}
                onUpdate={(updates) => updateCamera(camera.id, updates)}
              />
            ))}
          </div>
        </div>
      )}

      {cameras.length === 0 && (
        <div className="text-center py-8 text-gray-500">
          <Camera className="w-12 h-12 mx-auto mb-4 text-gray-600" />
          <p>No cameras configured. Add a camera to get started.</p>
        </div>
      )}
    </div>
  );
};

interface CameraPreviewProps {
  camera: CameraConfig;
  paused: boolean;
  onRemove: () => void;
  onUpdate: (updates: Partial<CameraConfig>) => void;
}

const CameraPreview: React.FC<CameraPreviewProps> = ({
  camera,
  paused,
  onRemove,
  onUpdate,
}) => {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [streamError, setStreamError] = useState(false);

  useEffect(() => {
    if (paused || !camera.device_id) {
      if (!camera.device_id) setStreamError(true);
      return;
    }
    let cancelled = false;
    let stream: MediaStream | null = null;

    (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { deviceId: { exact: camera.device_id } },
        });
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          await videoRef.current.play().catch(() => {});
        }
        setStreamError(false);
      } catch (err) {
        console.warn("Live preview failed for", camera.name, err);
        setStreamError(true);
      }
    })();

    return () => {
      cancelled = true;
      if (stream) stream.getTracks().forEach((t) => t.stop());
    };
  }, [camera.device_id, camera.name, paused]);

  const showVideo = !paused && camera.device_id && !streamError;
  return (
    <div className="bg-gray-900 rounded-lg border border-gray-700 overflow-hidden">
      <div className="aspect-[4/3] bg-gray-800 relative">
        {showVideo ? (
          <video
            ref={videoRef}
            autoPlay
            muted
            playsInline
            className="w-full h-full object-cover"
          />
        ) : (
          <div className="w-full h-full flex flex-col items-center justify-center">
            <VideoOff className="w-8 h-8 text-gray-500 mb-2" />
            <span className="text-gray-500 text-sm">
              {paused
                ? "Preview paused"
                : camera.device_id
                ? "Preview failed"
                : "No browser match"}
            </span>
          </div>
        )}
      </div>

      {/* Camera Info */}
      <div className="p-3 space-y-2">
        <div className="flex items-center justify-between">
          <h5 className="font-medium text-white truncate">{camera.name}</h5>
          <Button
            onClick={onRemove}
            size="sm"
            variant="ghost"
            className="text-red-400 hover:text-red-300 hover:bg-red-900/20 p-1"
          >
            <X className="w-4 h-4" />
          </Button>
        </div>

        <div className="grid grid-cols-1 gap-2 text-xs text-gray-400">
          <div className="flex items-center gap-2">
            <span className="w-16">Resolution:</span>
            <div className="flex items-center gap-1">
              <Input
                type="number"
                value={camera.width}
                onChange={(e) =>
                  onUpdate({ width: parseInt(e.target.value) || 640 })
                }
                className="bg-gray-800 border-gray-700 text-white text-xs h-6 px-2 w-16"
                min="320"
                max="1920"
              />
              <span className="flex items-center">×</span>
              <Input
                type="number"
                value={camera.height}
                onChange={(e) =>
                  onUpdate({ height: parseInt(e.target.value) || 480 })
                }
                className="bg-gray-800 border-gray-700 text-white text-xs h-6 px-2 w-16"
                min="240"
                max="1080"
              />
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className="w-16">FPS:</span>
            <Input
              type="number"
              value={camera.fps || 30}
              onChange={(e) =>
                onUpdate({ fps: parseInt(e.target.value) || 30 })
              }
              className="bg-gray-800 border-gray-700 text-white text-xs h-6 px-2 w-16"
              min="10"
              max="60"
            />
          </div>
        </div>

        <div className="text-xs text-gray-500">
          Type: {camera.type} | Device: {camera.device_id?.substring(0, 10)}...
        </div>
      </div>
    </div>
  );
};

export default CameraConfiguration;
