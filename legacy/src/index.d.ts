/// <reference types="node" />

export interface DvripOptions {
  host: string;
  port?: number;
  username?: string;
  password?: string;
  timeout?: number;
  /**
   * Target camera/channel index for per-channel talk on capable devices.
   * Most DVRs/NVRs have a single audio-out and ignore/reject this; to drive
   * one specific IP camera, point `host` at that camera's own IP instead.
   */
  channel?: number | null;
}

export interface TalkStartOptions {
  channel?: number | null;
  dataCmd?: number;
}

export interface SendOptions {
  packetSize?: number;
  onProgress?: (sent: number, total: number) => void;
}

export interface AudioOptions extends SendOptions {
  ffmpegPath?: string;
}

export interface TextOptions extends AudioOptions {
  voice?: string;
}

export function sofiaHash(password?: string): string;

export function toAlaw(
  input: string | Buffer,
  opts?: { ffmpegPath?: string }
): Promise<Buffer>;

export function textToAlaw(
  text: string,
  opts?: { voice?: string; ffmpegPath?: string }
): Promise<Buffer>;

export interface CameraInfo {
  channel: number;
  title: string;
  type: 'ip' | 'analog' | 'empty';
  enabled: boolean;
  configured: boolean;
  online: boolean;
  recording: boolean;
  bitrate: number;
  ip: string | null;
  port: number | null;
  protocol: string | null;
  username: string;
  /**
   * Stored remote-camera password. NOTE: many NVR firmwares return this EMPTY
   * on read for security even when a camera is configured — an empty string
   * usually means "masked by the device", not "no password".
   */
  password: string;
}

export interface CameraSpec {
  ip: string;
  port?: number;
  username?: string;
  password?: string;
  protocol?: string;
}

export interface PtzOptions {
  channel?: number;
  step?: number;
  preset?: number;
  stop?: boolean;
}

export type PtzCommand =
  | 'DirectionUp' | 'DirectionDown' | 'DirectionLeft' | 'DirectionRight'
  | 'DirectionLeftUp' | 'DirectionLeftDown' | 'DirectionRightUp' | 'DirectionRightDown'
  | 'ZoomTile' | 'ZoomWide' | 'FocusNear' | 'FocusFar'
  | 'IrisSmall' | 'IrisLarge'
  | 'SetPreset' | 'GotoPreset' | 'ClearPreset' | 'StartTour' | 'StopTour';

export interface RecordingSearch {
  channel?: number;
  start: string;
  end: string;
  type?: string;
}

export interface NvrControl {
  listCameras(): Promise<CameraInfo[]>;
  keepAlive(): Promise<Record<string, unknown>>;
  getSystemInfo(): Promise<any>;
  getWorkState(): Promise<any>;
  getStorageInfo(): Promise<any>;
  getNetworkConfig(): Promise<any>;
  getSystemFunction(): Promise<any>;
  getConfig(name: string, cmd?: number): Promise<any>;
  setConfig(name: string, value: any, cmd?: number): Promise<any>;
  getTime(): Promise<Date | null>;
  setTime(date?: Date): Promise<Record<string, unknown>>;
  getChannelTitles(): Promise<string[]>;
  setChannelTitles(titles: string[]): Promise<Record<string, unknown>>;
  setChannelTitle(channel: number, title: string): Promise<Record<string, unknown>>;
  getUsers(): Promise<any>;
  getGroups(): Promise<any>;
  reboot(): Promise<Record<string, unknown>>;
  shutdown(): Promise<Record<string, unknown>>;
  addCamera(channel: number, cam: CameraSpec): Promise<any>;
  removeCamera(channel: number): Promise<any>;
  ptz(command: PtzCommand, opts?: PtzOptions): Promise<Record<string, unknown>>;
  ptzStop(command: PtzCommand, opts?: PtzOptions): Promise<Record<string, unknown>>;
  searchRecordings(opts: RecordingSearch): Promise<any[]>;
  snapshot(channel?: number): Promise<Buffer>;
}

export class DvripClient implements NvrControl {
  constructor(opts: DvripOptions);
  session: number;
  channelCount: number;
  connect(): Promise<void>;
  login(): Promise<Record<string, unknown>>;
  startTalk(opts?: TalkStartOptions): Promise<Record<string, unknown>>;
  stopTalk(): void;
  sendAlaw(alaw: Buffer, opts?: SendOptions): Promise<number>;
  send(cmd: number, payload: Buffer): void;
  sendJson(cmd: number, obj: unknown): Promise<Record<string, unknown>>;
  close(): void;
  // NvrControl
  listCameras(): Promise<CameraInfo[]>;
  keepAlive(): Promise<Record<string, unknown>>;
  getSystemInfo(): Promise<any>;
  getWorkState(): Promise<any>;
  getStorageInfo(): Promise<any>;
  getNetworkConfig(): Promise<any>;
  getSystemFunction(): Promise<any>;
  getConfig(name: string, cmd?: number): Promise<any>;
  setConfig(name: string, value: any, cmd?: number): Promise<any>;
  getTime(): Promise<Date | null>;
  setTime(date?: Date): Promise<Record<string, unknown>>;
  getChannelTitles(): Promise<string[]>;
  setChannelTitles(titles: string[]): Promise<Record<string, unknown>>;
  setChannelTitle(channel: number, title: string): Promise<Record<string, unknown>>;
  getUsers(): Promise<any>;
  getGroups(): Promise<any>;
  reboot(): Promise<Record<string, unknown>>;
  shutdown(): Promise<Record<string, unknown>>;
  addCamera(channel: number, cam: CameraSpec): Promise<any>;
  removeCamera(channel: number): Promise<any>;
  ptz(command: PtzCommand, opts?: PtzOptions): Promise<Record<string, unknown>>;
  ptzStop(command: PtzCommand, opts?: PtzOptions): Promise<Record<string, unknown>>;
  searchRecordings(opts: RecordingSearch): Promise<any[]>;
  snapshot(channel?: number): Promise<Buffer>;
}

export class DvripTalk implements NvrControl {
  constructor(opts: DvripOptions);
  client: DvripClient;
  connect(): Promise<this>;
  playAlaw(alaw: Buffer, opts?: SendOptions): Promise<number>;
  playFile(input: string | Buffer, opts?: AudioOptions): Promise<number>;
  playText(text: string, opts?: TextOptions): Promise<number>;
  close(): void;
  // NvrControl (delegated to client)
  listCameras(): Promise<CameraInfo[]>;
  keepAlive(): Promise<Record<string, unknown>>;
  getSystemInfo(): Promise<any>;
  getWorkState(): Promise<any>;
  getStorageInfo(): Promise<any>;
  getNetworkConfig(): Promise<any>;
  getSystemFunction(): Promise<any>;
  getConfig(name: string, cmd?: number): Promise<any>;
  setConfig(name: string, value: any, cmd?: number): Promise<any>;
  getTime(): Promise<Date | null>;
  setTime(date?: Date): Promise<Record<string, unknown>>;
  getChannelTitles(): Promise<string[]>;
  setChannelTitles(titles: string[]): Promise<Record<string, unknown>>;
  setChannelTitle(channel: number, title: string): Promise<Record<string, unknown>>;
  getUsers(): Promise<any>;
  getGroups(): Promise<any>;
  reboot(): Promise<Record<string, unknown>>;
  shutdown(): Promise<Record<string, unknown>>;
  addCamera(channel: number, cam: CameraSpec): Promise<any>;
  removeCamera(channel: number): Promise<any>;
  ptz(command: PtzCommand, opts?: PtzOptions): Promise<Record<string, unknown>>;
  ptzStop(command: PtzCommand, opts?: PtzOptions): Promise<Record<string, unknown>>;
  searchRecordings(opts: RecordingSearch): Promise<any[]>;
  snapshot(channel?: number): Promise<Buffer>;
}

export interface BroadcastResult {
  host: string;
  ok: boolean;
  frames?: number;
  error?: string;
}

export function broadcast(
  targets: DvripOptions[],
  source: { text: string } | { file: string | Buffer } | { alaw: Buffer },
  defaults?: Partial<DvripOptions> & TextOptions
): Promise<BroadcastResult[]>;

/** Friendlier aliases matching the package name (same classes). */
export { DvripTalk as Xmeye, DvripClient as XmeyeClient };

export const CMD: {
  LOGIN: number;
  OPTALK_CLAIM: number;
  OPTALK_START: number;
  OPTALK_DATA: number;
};

export const RET: Record<number, string>;
