'use strict';

const net = require('net');
const { EventEmitter } = require('events');
const { sofiaHash } = require('./sofia');

// DVRIP / Sofia command (message) IDs — verified against go2rtc & XMEye SDK.
const CMD = {
  LOGIN: 1000,
  KEEPALIVE: 1006,
  CONFIG_SET: 1040,
  CONFIG_GET: 1042,
  OPPTZCONTROL: 1400,
  OPMONITOR_START: 1410,
  OPMONITOR_CLAIM: 1413,
  OPTALK_START: 1430,
  OPTALK_DATA: 1432,
  OPTALK_CLAIM: 1434,
  OPFILEQUERY: 1440,
  OPMACHINE: 1450,
  OPTIMEQUERY: 1452,
  USERS: 1472,
  OPSNAP: 1560,
  // AVTalk (channel-scoped talk on capable devices; experimental)
  AVTALK_CLAIM: 1417,
  AVTALK_DATA: 1419,
};

// Ret codes surfaced by the device (from the DVRIP/Sofia reference).
const RET = {
  100: 'OK',
  101: 'Unknown error',
  102: 'Unsupported version',
  103: 'Request not permitted',
  104: 'User already logged in',
  105: 'User is not logged in',
  106: 'Username or password is incorrect',
  107: 'User has no necessary permissions',
  110: 'Search success, returned all files',
  111: 'Search success, returned partial files',
  121: 'Digital channel is not enabled',
  203: 'Password is incorrect',
  205: 'Account is locked (too many failed logins)',
  213: 'Permission table error',
  503: 'Talk channel is already open',
  504: 'Talk channel is not open',
  511: 'Start of upgrade',
  512: 'Upgrade was not started',
  513: 'Upgrade data error',
  514: 'Upgrade error',
  515: 'Upgrade successful',
  602: 'Application restart required for change to take effect',
  603: 'System (device) restart required for change to take effect',
  604: 'Write file error',
  605: 'Feature not supported',
  606: 'Verification failed (device rejected the value/credentials)',
  607: 'Config name does not exist',
  608: 'Config parse error',
};

const HEADER_LEN = 20;

/**
 * Low-level DVRIP client with just enough of the protocol to log in and push
 * audio out of the device speaker via the OPTalk backchannel.
 *
 * Events:
 *   'error'  (err)
 *   'close'
 */
class DvripClient extends EventEmitter {
  /**
   * @param {object} opts
   * @param {string} opts.host
   * @param {number} [opts.port=34567]
   * @param {string} [opts.username='admin']
   * @param {string} [opts.password='']
   * @param {number} [opts.timeout=8000] socket timeout in ms
   */
  constructor(opts = {}) {
    super();
    if (!opts.host) throw new Error('host is required');
    this.host = opts.host;
    this.port = opts.port || 34567;
    this.username = opts.username || 'admin';
    this.password = opts.password || '';
    this.timeout = opts.timeout || 8000;

    this.session = 0;
    this.seq = 0;
    this._sock = null;
    this._buf = Buffer.alloc(0);
    this._waiters = []; // queue of {resolve, reject} awaiting a JSON reply
  }

  /** Open the TCP connection. */
  connect() {
    return new Promise((resolve, reject) => {
      const sock = net.createConnection({ host: this.host, port: this.port });
      sock.setTimeout(this.timeout);
      const onErr = (err) => {
        sock.destroy();
        reject(err);
      };
      sock.once('error', onErr);
      sock.once('timeout', () => onErr(new Error('connect timeout')));
      sock.once('connect', () => {
        sock.removeListener('error', onErr);
        sock.on('error', (e) => this.emit('error', e));
        sock.on('data', (chunk) => this._onData(chunk));
        sock.on('close', () => this.emit('close'));
        this._sock = sock;
        resolve();
      });
    });
  }

  _onData(chunk) {
    this._buf = Buffer.concat([this._buf, chunk]);
    // Parse as many complete DVRIP messages as are buffered.
    while (this._buf.length >= HEADER_LEN) {
      if (this._buf[0] !== 0xff) {
        // Resync: drop a byte. Should not happen on a healthy stream.
        this._buf = this._buf.subarray(1);
        continue;
      }
      this.session = this._buf.readUInt32LE(4);
      const len = this._buf.readUInt32LE(16);
      if (this._buf.length < HEADER_LEN + len) break; // wait for more
      const payload = this._buf.subarray(HEADER_LEN, HEADER_LEN + len);
      this._buf = this._buf.subarray(HEADER_LEN + len);
      if (this._collector) this._collector(Buffer.from(payload));
      else this._deliver(payload);
    }
  }

  _deliver(payload) {
    // Strip trailing newline / null terminators, then parse JSON.
    let end = payload.length;
    while (end > 0 && (payload[end - 1] === 0x00 || payload[end - 1] === 0x0a)) {
      end--;
    }
    const text = payload.subarray(0, end).toString('utf8');
    const waiter = this._waiters.shift();
    if (!waiter) return; // unsolicited (e.g. media) — ignore
    try {
      waiter.resolve(JSON.parse(text));
    } catch (e) {
      waiter.resolve({ _raw: text });
    }
  }

  /** Build a DVRIP frame header + payload buffer. */
  _frame(cmd, payload) {
    const head = Buffer.alloc(HEADER_LEN);
    head[0] = 0xff;
    head.writeUInt32LE(this.session >>> 0, 4);
    head.writeUInt32LE(this.seq >>> 0, 8);
    head.writeUInt16LE(cmd, 14);
    head.writeUInt32LE(payload.length, 16);
    this.seq = (this.seq + 1) >>> 0;
    return Buffer.concat([head, payload]);
  }

  /** Send a raw command with a binary payload; does not wait for a reply. */
  send(cmd, payload) {
    if (!this._sock) throw new Error('not connected');
    this._sock.write(this._frame(cmd, payload));
  }

  /** Send a JSON command and resolve with the device's JSON reply. */
  sendJson(cmd, obj) {
    const payload = Buffer.concat([
      Buffer.from(JSON.stringify(obj), 'utf8'),
      Buffer.from([0x0a, 0x00]),
    ]);
    const p = new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        const idx = this._waiters.indexOf(w);
        if (idx >= 0) this._waiters.splice(idx, 1);
        reject(new Error(`timeout waiting for reply to cmd ${cmd}`));
      }, this.timeout);
      const w = {
        resolve: (v) => {
          clearTimeout(timer);
          resolve(v);
        },
        reject: (e) => {
          clearTimeout(timer);
          reject(e);
        },
      };
      this._waiters.push(w);
    });
    this.send(cmd, payload);
    return p;
  }

  /** Authenticate. Throws with a decoded Ret message on failure. */
  async login() {
    const res = await this.sendJson(CMD.LOGIN, {
      EncryptType: 'MD5',
      LoginType: 'DVRIP-Web',
      PassWord: sofiaHash(this.password),
      UserName: this.username,
    });
    if (res.Ret !== 100) {
      const msg = RET[res.Ret] || `Ret ${res.Ret}`;
      const err = new Error(`login failed: ${msg}`);
      err.ret = res.Ret;
      throw err;
    }
    this.session = parseInt(res.SessionID, 16);
    this.channelCount = res.ChannelNum || 0;
    return res;
  }

  /**
   * Read a config/state tree by name (DVRIP ConfigGet). Returns the value under
   * `name` (e.g. the array/object), or the whole reply if the key is absent.
   * @param {string} name e.g. 'NetWork.RemoteDevice', 'WorkState', 'SystemInfo'
   * @param {number} [cmd=1042] message id (1042 = ConfigGet, 1020 = SystemInfo group)
   */
  async getConfig(name, cmd = 1042) {
    const res = await this.sendJson(cmd, { Name: name, SessionID: this._sid() });
    if (res.Ret !== undefined && res.Ret !== 100) {
      const err = new Error(`get "${name}" failed: ${RET[res.Ret] || 'Ret ' + res.Ret}`);
      err.ret = res.Ret;
      throw err;
    }
    return name in res ? res[name] : res;
  }

  /**
   * Enumerate the NVR/DVR's channels and any IP cameras attached to them.
   *
   * Merges three sources:
   *   - NetWork.RemoteDevice : per-channel IP camera config (ip/port/user/enable)
   *   - ChannelTitle         : channel display names
   *   - WorkState            : live per-channel bitrate + recording flag
   *
   * @returns {Promise<Array<{
   *   channel:number, title:string, type:'ip'|'analog'|'empty',
   *   enabled:boolean, online:boolean, recording:boolean, bitrate:number,
   *   ip:string|null, port:number|null, protocol:string|null, username:string|null
   * }>>}
   */
  async listCameras() {
    const [remote, titles, workState] = await Promise.all([
      this.getConfig('NetWork.RemoteDevice').catch(() => []),
      this.getConfig('ChannelTitle', 1048).catch(() => []),
      this.getConfig('WorkState', 1020).catch(() => ({})),
    ]);

    const remotes = Array.isArray(remote) ? remote : [];
    const names = Array.isArray(titles) ? titles : [];
    const states = (workState && workState.ChannelState) || [];

    // How many channels does this device actually have?
    const count =
      this.channelCount ||
      Math.max(names.length, remotes.length, states.length) ||
      0;

    // The NVR uses this placeholder IP for unconfigured remote slots.
    const PLACEHOLDER_IP = '192.168.0.10';

    const cams = [];
    for (let i = 0; i < count; i++) {
      const r = remotes.find((x) => x.Channel === i) || remotes[i] || {};
      const st = states[i] || {};
      const bitrate = st.Bitrate || 0;
      const enabled = !!r.Enable;

      // A remote (IP-camera) slot is "configured" only if it's enabled or points
      // at a real (non-placeholder, non-empty) address. Unconfigured slots carry
      // the firmware's factory default (e.g. 192.168.0.10) — that is NOT a real
      // camera IP and must not be reported as one.
      const rawIp = r.IPAddress || '';
      const realIp = rawIp && rawIp !== PLACEHOLDER_IP && rawIp !== '0.0.0.0';
      const configured = enabled || !!realIp;

      let type = 'empty';
      if (configured) type = 'ip';
      else if (bitrate > 0 || st.Record) type = 'analog';

      // Only surface address/credentials for a genuinely configured IP camera.
      // NOTE: many NVR firmwares still return an EMPTY `password` on read for a
      // configured camera (masked for security) — empty means "device won't
      // reveal it", not "no password".
      cams.push({
        channel: i,
        title: names[i] || '',
        type,
        enabled,
        configured,
        online: type === 'ip' ? bitrate > 0 : type === 'analog',
        recording: !!st.Record,
        bitrate,
        ip: configured ? r.IPAddress || null : null,
        port: configured ? r.Port || null : null,
        protocol: configured ? r.Protocol || null : null,
        username: configured ? r.UserName || '' : '',
        password: configured ? r.PassWord || '' : '',
      });
    }
    return cams;
  }

  _sid() {
    return '0x' + (this.session >>> 0).toString(16).padStart(8, '0').toUpperCase();
  }

  // ---------------------------------------------------------------------------
  // NVR / DVR control
  // ---------------------------------------------------------------------------

  /** Send a config-set (ConfigSet, cmd 1040). Throws on non-100 Ret. */
  async setConfig(name, value, cmd = 1040) {
    const res = await this.sendJson(cmd, {
      Name: name,
      SessionID: this._sid(),
      [name]: value,
    });
    if (res.Ret !== undefined && res.Ret !== 100) {
      const err = new Error(`set "${name}" failed: ${RET[res.Ret] || 'Ret ' + res.Ret}`);
      err.ret = res.Ret;
      throw err;
    }
    return res;
  }

  /** Keep the session alive (call periodically, e.g. every ~20s). */
  keepAlive() {
    return this.sendJson(CMD.KEEPALIVE, { Name: 'KeepAlive', SessionID: this._sid() });
  }

  /** Full SystemInfo block (model, firmware, serial, channel counts, uptime). */
  getSystemInfo() {
    return this.getConfig('SystemInfo', 1020);
  }

  /** Per-channel live state: [{ Bitrate, Record }, ...] plus alarm flags. */
  getWorkState() {
    return this.getConfig('WorkState', 1020);
  }

  /** Storage / HDD info, including partitions and recorded time ranges. */
  getStorageInfo() {
    return this.getConfig('StorageInfo', 1020);
  }

  /** Network config (IP, gateway, ports, MAC). */
  getNetworkConfig() {
    return this.getConfig('NetWork.NetCommon');
  }

  /** Device capability flags. */
  getSystemFunction() {
    return this.getConfig('SystemFunction', 1360);
  }

  /** Read the device clock as a JS Date. */
  async getTime() {
    const res = await this.sendJson(1452, { Name: 'OPTimeQuery', SessionID: this._sid() });
    const s = res.OPTimeQuery;
    return s ? new Date(s.replace(' ', 'T')) : null;
  }

  /**
   * Set the device clock.
   * @param {Date} [date=now]
   */
  setTime(date = new Date()) {
    const p = (n) => String(n).padStart(2, '0');
    const s =
      `${date.getFullYear()}-${p(date.getMonth() + 1)}-${p(date.getDate())} ` +
      `${p(date.getHours())}:${p(date.getMinutes())}:${p(date.getSeconds())}`;
    // OPTimeSetting shares command id 1450 with OPMachine.
    return this.sendJson(1450, {
      Name: 'OPTimeSetting',
      SessionID: this._sid(),
      OPTimeSetting: s,
    });
  }

  /** Channel display names, e.g. ['Front','Back',...]. */
  async getChannelTitles() {
    const t = await this.getConfig('ChannelTitle', 1048);
    return Array.isArray(t) ? t : [];
  }

  /** Set all channel titles at once (array of strings). */
  setChannelTitles(titles) {
    return this.sendJson(1046, {
      Name: 'ChannelTitle',
      SessionID: this._sid(),
      ChannelTitle: titles,
    });
  }

  /** Set a single channel's title (reads current titles, updates one). */
  async setChannelTitle(channel, title) {
    const titles = await this.getChannelTitles();
    while (titles.length <= channel) titles.push('');
    titles[channel] = title;
    return this.setChannelTitles(titles);
  }

  /** List device users (accounts). */
  getUsers() {
    return this.getConfig('Users', 1472);
  }

  /** List permission groups. */
  getGroups() {
    return this.getConfig('Groups', 1474);
  }

  /** Reboot the device. Destructive — the connection will drop. */
  reboot() {
    return this.sendJson(1450, {
      Name: 'OPMachine',
      SessionID: this._sid(),
      OPMachine: { Action: 'Reboot' },
    });
  }

  /** Shut the device down. Destructive. */
  shutdown() {
    return this.sendJson(1450, {
      Name: 'OPMachine',
      SessionID: this._sid(),
      OPMachine: { Action: 'Shutdown' },
    });
  }

  /**
   * Register/enable an IP camera on a channel (writes NetWork.RemoteDevice).
   * @param {number} channel
   * @param {object} cam { ip, port=34567, username='admin', password='', protocol='TCP' }
   */
  async addCamera(channel, cam) {
    const table = await this.getConfig('NetWork.RemoteDevice');
    const list = Array.isArray(table) ? table : [];
    const entry = {
      Channel: channel,
      Enable: true,
      IPAddress: cam.ip,
      Port: cam.port || 34567,
      Protocol: cam.protocol || 'TCP',
      UserName: cam.username || 'admin',
      PassWord: cam.password || '',
    };
    const idx = list.findIndex((x) => x.Channel === channel);
    if (idx >= 0) list[idx] = { ...list[idx], ...entry };
    else list.push(entry);
    try {
      return await this.setConfig('NetWork.RemoteDevice', list);
    } catch (e) {
      if (e.ret === 606) {
        e.message +=
          ' — some NVRs (esp. hybrid DVRs) reject a plain RemoteDevice write and ' +
          'require adding the camera via the device UI/app, and/or a reboot. ' +
          'Check the camera IP/credentials are correct and reachable.';
      }
      throw e;
    }
  }

  /** Disable/clear the IP camera on a channel. */
  async removeCamera(channel) {
    const table = await this.getConfig('NetWork.RemoteDevice');
    const list = Array.isArray(table) ? table : [];
    const idx = list.findIndex((x) => x.Channel === channel);
    if (idx >= 0) list[idx] = { ...list[idx], Enable: false };
    return this.setConfig('NetWork.RemoteDevice', list);
  }

  /**
   * PTZ control. Sends a movement command; for continuous moves call again with
   * `stop: true` (or use ptzStop) to halt.
   * @param {string} command DirectionUp/Down/Left/Right (+diagonals),
   *   ZoomTile/ZoomWide, FocusNear/FocusFar, IrisSmall/IrisLarge,
   *   SetPreset/GotoPreset/ClearPreset, StartTour/StopTour
   * @param {object} [opts] { channel=0, step=5, preset=-1, stop=false }
   */
  ptz(command, opts = {}) {
    const parameter = {
      AUX: { Number: 0, Status: 'On' },
      Channel: opts.channel || 0,
      MenuOpts: 'Enter',
      POINT: { bottom: 0, left: 0, right: 0, top: 0 },
      Pattern: opts.stop ? 'Stop' : 'SetBegin',
      Preset: opts.preset == null ? -1 : opts.preset,
      Step: opts.step == null ? 5 : opts.step,
      Tour: /Tour/.test(command) ? 1 : 0,
    };
    return this.sendJson(1400, {
      Name: 'OPPTZControl',
      SessionID: this._sid(),
      OPPTZControl: { Command: command, Parameter: parameter },
    });
  }

  /** Stop a continuous PTZ movement started by ptz(). */
  ptzStop(command, opts = {}) {
    return this.ptz(command, { ...opts, stop: true });
  }

  /**
   * Search recorded files on the device.
   * @param {object} opts { channel=0, start, end, type='h264' }
   *   start/end are "YYYY-MM-DD hh:mm:ss".
   * @returns {Promise<Array>} matched file records (first page, up to 64)
   */
  async searchRecordings(opts) {
    const res = await this.sendJson(1440, {
      Name: 'OPFileQuery',
      SessionID: this._sid(),
      OPFileQuery: {
        BeginTime: opts.start,
        EndTime: opts.end,
        Channel: opts.channel || 0,
        DriverTypeMask: '0x0000FFFF',
        Event: '*',
        StreamType: '0x00000000',
        Type: opts.type || 'h264',
      },
    });
    return Array.isArray(res.OPFileQuery) ? res.OPFileQuery : [];
  }

  /**
   * Capture a single JPEG snapshot from a channel.
   * @param {number} [channel=0]
   * @returns {Promise<Buffer>} JPEG image bytes
   */
  snapshot(channel = 0) {
    return new Promise((resolve, reject) => {
      let acc = Buffer.alloc(0);
      const timer = setTimeout(() => {
        this._collector = null;
        reject(new Error('snapshot timeout'));
      }, this.timeout);

      this._collector = (payload) => {
        acc = Buffer.concat([acc, payload]);
        const soi = acc.indexOf(Buffer.from([0xff, 0xd8]));
        if (soi >= 0) {
          const eoi = acc.indexOf(Buffer.from([0xff, 0xd9]), soi + 2);
          if (eoi >= 0) {
            clearTimeout(timer);
            this._collector = null;
            resolve(acc.subarray(soi, eoi + 2));
          }
        }
      };

      try {
        this.send(
          CMD.OPSNAP,
          Buffer.concat([
            Buffer.from(
              JSON.stringify({
                Name: 'OPSNAP',
                SessionID: this._sid(),
                OPSNAP: { Channel: channel },
              }),
              'utf8'
            ),
            Buffer.from([0x0a, 0x00]),
          ])
        );
      } catch (e) {
        clearTimeout(timer);
        this._collector = null;
        reject(e);
      }
    });
  }

  /**
   * Claim + start the talk backchannel so audio frames will be played.
   *
   * @param {object} [opts]
   * @param {number|null} [opts.channel=null] target channel/camera index.
   *   - `null` (default): standard OPTalk -> the device's single audio-out.
   *     This is the only mode verified across most Xiongmai DVRs/NVRs.
   *   - a number: tries OPTalk with a `Channel` field for firmwares that
   *     support per-channel routing. Many devices reject this (Ret 103); to
   *     drive one specific IP camera, prefer connecting directly to that
   *     camera's IP instead (see README "Multiple cameras").
   * @param {number} [opts.dataCmd] override the audio-frame command id.
   */
  async startTalk(opts = {}) {
    const channel = opts.channel == null ? null : opts.channel;
    this.talkDataCmd = opts.dataCmd || CMD.OPTALK_DATA;

    const optalk = { Action: 'Claim', AudioFormat: { EncodeType: 'G711_ALAW' } };
    if (channel != null) optalk.Channel = channel;

    const claim = await this.sendJson(CMD.OPTALK_CLAIM, {
      Name: 'OPTalk',
      SessionID: this._sid(),
      OPTalk: optalk,
    });
    if (claim.Ret !== 100) {
      let hint = RET[claim.Ret] || 'Ret ' + claim.Ret;
      if (channel != null && claim.Ret === 103) {
        hint +=
          ' — this device does not support per-channel talk. Connect ' +
          'directly to the target camera\'s IP instead (see README).';
      }
      const err = new Error(`OPTalk claim failed: ${hint}`);
      err.ret = claim.Ret;
      throw err;
    }
    // Start does not send back a JSON reply we need to await.
    const startBody = { Action: 'Start', AudioFormat: { EncodeType: 'G711_ALAW' } };
    if (channel != null) startBody.Channel = channel;
    this.send(
      CMD.OPTALK_START,
      Buffer.concat([
        Buffer.from(
          JSON.stringify({
            Name: 'OPTalk',
            SessionID: this._sid(),
            OPTalk: startBody,
          }),
          'utf8'
        ),
        Buffer.from([0x0a, 0x00]),
      ])
    );
    return claim;
  }

  /** Tell the device to stop the talk channel. */
  stopTalk() {
    if (!this._sock) return;
    this.send(
      CMD.OPTALK_START,
      Buffer.concat([
        Buffer.from(
          JSON.stringify({
            Name: 'OPTalk',
            SessionID: this._sid(),
            OPTalk: { Action: 'Stop', AudioFormat: { EncodeType: 'G711_ALAW' } },
          }),
          'utf8'
        ),
        Buffer.from([0x0a, 0x00]),
      ])
    );
  }

  /**
   * Stream a raw G.711 A-law (8 kHz mono) buffer to the speaker, paced in
   * real time. Call startTalk() first.
   *
   * @param {Buffer} alaw raw a-law samples (no WAV header)
   * @param {object} [opts]
   * @param {number} [opts.packetSize=320] samples/bytes per frame (40ms @ 8kHz)
   * @param {(sent:number,total:number)=>void} [opts.onProgress]
   */
  async sendAlaw(alaw, opts = {}) {
    const PKT = opts.packetSize || 320;
    const onProgress = opts.onProgress;

    // Pad to a whole number of frames with A-law silence (0xD5).
    let data = alaw;
    if (data.length % PKT !== 0) {
      const pad = Buffer.alloc(PKT - (data.length % PKT), 0xd5);
      data = Buffer.concat([data, pad]);
    }

    // 8-byte media header: 00 00 01 FA | codec | rate | len(LE16)
    const hdr = Buffer.alloc(8);
    hdr.writeUInt32BE(0x000001fa, 0);
    hdr[4] = 14; // 0x0E = G.711 A-law (use 10 / 0x0A for u-law)
    hdr[5] = 2; //  sample-rate index -> 8000 Hz
    hdr.writeUInt16LE(PKT, 6);

    const total = data.length / PKT;
    const frameMs = (PKT / 8000) * 1000; // 40ms for PKT=320
    const start = Date.now();

    const dataCmd = this.talkDataCmd || CMD.OPTALK_DATA;
    for (let i = 0, n = 0; i < data.length; i += PKT, n++) {
      this.send(dataCmd, Buffer.concat([hdr, data.subarray(i, i + PKT)]));
      if (onProgress) onProgress(n + 1, total);
      const target = start + (n + 1) * frameMs;
      const wait = target - Date.now();
      if (wait > 0) await new Promise((r) => setTimeout(r, wait));
    }
    return total;
  }

  /** Close the socket. */
  close() {
    if (this._sock) {
      this._sock.end();
      this._sock.destroy();
      this._sock = null;
    }
  }
}

module.exports = { DvripClient, CMD, RET };
