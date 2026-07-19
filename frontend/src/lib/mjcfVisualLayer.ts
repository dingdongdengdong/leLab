import {
  Color,
  Group,
  LoadingManager,
  Material,
  Mesh,
  Object3D,
  Quaternion,
  Vector3,
} from "three";

import { loadMeshFile } from "@/lib/meshLoaders";
import { URDFViewerElement } from "@/lib/urdfViewerHelpers";

export type Vec3Tuple = [number, number, number];
export type QuatWxyzTuple = [number, number, number, number];

export interface MjcfBodyPose {
  position_m: Vec3Tuple;
  quaternion_wxyz: QuatWxyzTuple;
}

export interface MjcfVisualPose {
  sequence?: number;
  timestamp: number;
  root_link?: string;
  bodies: Record<string, MjcfBodyPose>;
}

export interface MjcfVisualGeometry {
  mesh_url: string;
  position_m: Vec3Tuple;
  quaternion_wxyz: QuatWxyzTuple;
  scale: Vec3Tuple;
  rgba?: [number, number, number, number];
}

export interface MjcfVisualBody {
  name: string;
  visuals: MjcfVisualGeometry[];
}

export interface MjcfVisualManifest {
  root_link: string;
  bodies: MjcfVisualBody[];
  default_pose?: MjcfVisualPose;
  hand_joint_names: string[];
}

type MeshLoader = (url: string) => Promise<Object3D>;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const finiteNumber = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);

const tuple = <T extends number[]>(value: unknown, length: number): T | null =>
  Array.isArray(value) && value.length === length && value.every(finiteNumber)
    ? (value as T)
    : null;

const valueFrom = (record: Record<string, unknown>, ...keys: string[]) => {
  for (const key of keys) {
    if (record[key] !== undefined) return record[key];
  }
  return undefined;
};

const parseBodyPose = (value: unknown): MjcfBodyPose | null => {
  if (!isRecord(value)) return null;
  const position = tuple<Vec3Tuple>(
    valueFrom(value, "position_m", "position", "pos"),
    3,
  );
  const rawQuaternion = tuple<QuatWxyzTuple>(
    valueFrom(value, "quaternion_wxyz", "quaternion", "quat"),
    4,
  );
  if (!position || !rawQuaternion) return null;
  const norm = Math.hypot(...rawQuaternion);
  if (!Number.isFinite(norm) || norm < 1e-8) return null;
  return {
    position_m: [...position],
    quaternion_wxyz: rawQuaternion.map((entry) => entry / norm) as QuatWxyzTuple,
  };
};

const normalizeTimestampMs = (value: unknown) => {
  if (!finiteNumber(value)) return Date.now();
  return value > 1e11 ? value : value * 1000;
};

export const normalizeVisualPose = (value: unknown): MjcfVisualPose | null => {
  if (!isRecord(value)) return null;
  const bodyValue = value.bodies;
  if (!isRecord(bodyValue)) return null;
  const bodies: Record<string, MjcfBodyPose> = {};
  for (const [name, poseValue] of Object.entries(bodyValue)) {
    const pose = parseBodyPose(poseValue);
    if (!pose) return null;
    bodies[name] = pose;
  }
  if (Object.keys(bodies).length === 0) return null;
  return {
    sequence: finiteNumber(value.sequence) ? value.sequence : undefined,
    timestamp: normalizeTimestampMs(valueFrom(value, "timestamp", "timestamp_s", "time")),
    root_link: typeof value.root_link === "string" ? value.root_link : undefined,
    bodies,
  };
};

export const extractVisualPose = (message: unknown): MjcfVisualPose | null => {
  if (!isRecord(message)) return null;
  const direct = normalizeVisualPose(message.visual_pose);
  if (direct) return direct;
  return isRecord(message.state) ? normalizeVisualPose(message.state.visual_pose) : null;
};

const parseVisual = (value: unknown): MjcfVisualGeometry | null => {
  if (!isRecord(value)) return null;
  const meshUrl = valueFrom(value, "mesh_url", "url", "mesh");
  const position = tuple<Vec3Tuple>(valueFrom(value, "position_m", "position", "pos"), 3);
  const quaternion = tuple<QuatWxyzTuple>(
    valueFrom(value, "quaternion_wxyz", "quaternion", "quat"),
    4,
  );
  const scale = tuple<Vec3Tuple>(value.scale, 3) || [1, 1, 1];
  const rgba = tuple<[number, number, number, number]>(valueFrom(value, "rgba", "color"), 4);
  if (typeof meshUrl !== "string" || !meshUrl || !position || !quaternion) return null;
  const normalizedPose = parseBodyPose({ position_m: position, quaternion_wxyz: quaternion });
  if (!normalizedPose) return null;
  return {
    mesh_url: meshUrl,
    position_m: normalizedPose.position_m,
    quaternion_wxyz: normalizedPose.quaternion_wxyz,
    scale: [...scale],
    rgba: rgba ? [...rgba] : undefined,
  };
};

export const normalizeVisualManifest = (value: unknown): MjcfVisualManifest | null => {
  if (!isRecord(value)) return null;
  const rootLink = valueFrom(value, "root_link", "root_body");
  if (typeof rootLink !== "string" || !rootLink) return null;

  const bodies: MjcfVisualBody[] = [];
  const rawBodies = value.bodies;
  const bodyEntries: [string, unknown][] = Array.isArray(rawBodies)
    ? rawBodies.map((body, index) => [isRecord(body) && typeof body.name === "string" ? body.name : String(index), body])
    : isRecord(rawBodies)
      ? Object.entries(rawBodies)
      : [];
  for (const [fallbackName, rawBody] of bodyEntries) {
    if (!isRecord(rawBody)) return null;
    const name = typeof rawBody.name === "string" ? rawBody.name : fallbackName;
    const rawVisuals = valueFrom(rawBody, "visuals", "geometries", "geoms");
    if (!Array.isArray(rawVisuals)) return null;
    const visuals = rawVisuals.map(parseVisual);
    if (visuals.some((visual) => !visual)) return null;
    bodies.push({ name, visuals: visuals as MjcfVisualGeometry[] });
  }
  if (bodies.length === 0) return null;

  const defaultPose = normalizeVisualPose(valueFrom(value, "default_pose", "open_pose"));
  const handJointNames = Array.isArray(value.hand_joint_names)
    ? value.hand_joint_names.filter((name): name is string => typeof name === "string")
    : [];
  return {
    root_link: rootLink,
    bodies,
    default_pose: defaultPose || undefined,
    hand_joint_names: handJointNames,
  };
};

export const findUrdfAttachment = (
  viewer: Pick<URDFViewerElement, "robot">,
  rootLink: string,
) => viewer.robot?.links?.[rootLink] || viewer.robot?.getObjectByName(rootLink) || null;

export const isAmazingHandJoint = (jointName: string, manifestNames: string[] = []) =>
  manifestNames.includes(jointName) || /^finger[1-4]_motor[12]$/.test(jointName);

export const filterScalarJoints = (
  joints: Record<string, number>,
  exactOverlayActive: boolean,
  handJointNames: string[] = [],
) => exactOverlayActive
  ? Object.fromEntries(Object.entries(joints).filter(([name]) => !isAmazingHandJoint(name, handJointNames)))
  : joints;

export const wxyzToThreeQuaternion = ([w, x, y, z]: QuatWxyzTuple) =>
  new Quaternion(x, y, z, w);

export const loadUniqueMeshes = async (
  urls: string[],
  loader: MeshLoader,
) => {
  const cache = new Map<string, Promise<Object3D>>();
  urls.forEach((url) => {
    if (!cache.has(url)) cache.set(url, loader(url));
  });
  const meshes = new Map<string, Object3D>();
  await Promise.all([...cache].map(async ([url, pending]) => meshes.set(url, await pending)));
  return meshes;
};

const defaultMeshLoader: MeshLoader = (url) => new Promise((resolve, reject) => {
  loadMeshFile(url, new LoadingManager(), (object, error) => {
    if (error || !object) reject(error || new Error(`Mesh unavailable: ${url}`));
    else resolve(object);
  });
});

const cloneMaterials = (object: Object3D, rgba?: [number, number, number, number]) => {
  object.traverse((child) => {
    if (!(child instanceof Mesh)) return;
    const originals = Array.isArray(child.material) ? child.material : [child.material];
    const copies = originals.map((material) => {
      const copy = material.clone();
      if (rgba && "color" in copy) {
        (copy as Material & { color: Color }).color.setRGB(rgba[0], rgba[1], rgba[2]);
        copy.opacity = rgba[3];
        copy.transparent = rgba[3] < 1;
      }
      return copy;
    });
    child.material = Array.isArray(child.material) ? copies : copies[0];
  });
};

export class MjcfVisualLayer {
  readonly root = new Group();
  readonly bodyGroups = new Map<string, Group>();

  private constructor(
    private readonly viewer: URDFViewerElement,
    readonly manifest: MjcfVisualManifest,
  ) {
    this.root.name = "amazinghand-mjcf-visual-layer";
  }

  static async create(
    viewer: URDFViewerElement,
    manifest: MjcfVisualManifest,
    resolveUrl: (url: string) => string,
    loader: MeshLoader = defaultMeshLoader,
  ) {
    const attachment = findUrdfAttachment(viewer, manifest.root_link);
    if (!attachment) throw new Error(`URDF attachment link not found: ${manifest.root_link}`);
    const layer = new MjcfVisualLayer(viewer, manifest);
    const urls = manifest.bodies.flatMap((body) => body.visuals.map((visual) => resolveUrl(visual.mesh_url)));
    const meshes = await loadUniqueMeshes(urls, loader);

    for (const body of manifest.bodies) {
      const group = new Group();
      group.name = `mjcf-body:${body.name}`;
      layer.bodyGroups.set(body.name, group);
      layer.root.add(group);
      for (const visual of body.visuals) {
        const url = resolveUrl(visual.mesh_url);
        const source = meshes.get(url);
        if (!source) throw new Error(`Loaded mesh missing from cache: ${url}`);
        const instance = source.clone(true);
        cloneMaterials(instance, visual.rgba);
        instance.position.fromArray(visual.position_m);
        instance.quaternion.copy(wxyzToThreeQuaternion(visual.quaternion_wxyz));
        instance.scale.fromArray(visual.scale);
        group.add(instance);
      }
    }
    attachment.add(layer.root);
    if (manifest.default_pose) layer.applyPose(manifest.default_pose);
    viewer.redraw();
    return layer;
  }

  applyPose(pose: MjcfVisualPose) {
    for (const [name, bodyPose] of Object.entries(pose.bodies)) {
      const group = this.bodyGroups.get(name);
      if (!group) continue;
      group.position.fromArray(bodyPose.position_m);
      group.quaternion.copy(wxyzToThreeQuaternion(bodyPose.quaternion_wxyz));
    }
    this.viewer.redraw();
  }

  dispose() {
    this.root.removeFromParent();
    this.root.traverse((child) => {
      if (!(child instanceof Mesh)) return;
      const materials = Array.isArray(child.material) ? child.material : [child.material];
      materials.forEach((material) => material.dispose());
    });
    this.bodyGroups.clear();
    this.viewer.redraw();
  }
}

const interpolatePose = (from: MjcfVisualPose, to: MjcfVisualPose, alpha: number): MjcfVisualPose => {
  const bodies: Record<string, MjcfBodyPose> = {};
  for (const [name, toPose] of Object.entries(to.bodies)) {
    const fromPose = from.bodies[name];
    if (!fromPose) {
      bodies[name] = toPose;
      continue;
    }
    const position = new Vector3(...fromPose.position_m).lerp(new Vector3(...toPose.position_m), alpha);
    const quaternion = wxyzToThreeQuaternion(fromPose.quaternion_wxyz)
      .slerp(wxyzToThreeQuaternion(toPose.quaternion_wxyz), alpha);
    bodies[name] = {
      position_m: position.toArray() as Vec3Tuple,
      quaternion_wxyz: [quaternion.w, quaternion.x, quaternion.y, quaternion.z],
    };
  }
  return { ...to, bodies };
};

export class VisualPoseBuffer {
  private previous: MjcfVisualPose | null = null;
  private current: MjcfVisualPose | null = null;
  private latestReceivedAtMs = 0;

  push(pose: MjcfVisualPose, receivedAtMs = performance.now()) {
    if (this.current && pose.timestamp <= this.current.timestamp) return false;
    this.previous = this.current;
    this.current = pose;
    this.latestReceivedAtMs = receivedAtMs;
    return true;
  }

  sample(receivedAtMs = performance.now(), delayMs = 50) {
    if (!this.current) return null;
    if (!this.previous) return this.current;
    const target = this.current.timestamp + (receivedAtMs - this.latestReceivedAtMs) - delayMs;
    if (target <= this.previous.timestamp) return this.previous;
    if (target >= this.current.timestamp) return this.current;
    const alpha = (target - this.previous.timestamp) / (this.current.timestamp - this.previous.timestamp);
    return interpolatePose(this.previous, this.current, alpha);
  }
}
