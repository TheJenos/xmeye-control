'use strict';

const { DvripClient, CMD, RET } = require('./client');
const { toAlaw, textToAlaw } = require('./audio');
const { sofiaHash } = require('./sofia');

/**
 * High-level convenience wrapper: connect, log in, and play audio out of the
 * device speaker in one object.
 *
 * @example
 *   const { DvripTalk } = require('xmeye-control');
 *   const talk = new DvripTalk({ host: '192.168.1.2', username: 'nadun', password: 'nadun1234' });
 *   await talk.connect();
 *   await talk.playFile('doorbell.mp3');
 *   await talk.playText('Please leave the package at the door');
 *   talk.close();
 */
class DvripTalk {
  constructor(opts = {}) {
    this.opts = opts;
    this.client = new DvripClient(opts);
    this._talkStarted = false;
  }

  /** Connect and authenticate. */
  async connect() {
    await this.client.connect();
    await this.client.login();
    return this;
  }

  async _ensureTalk() {
    if (!this._talkStarted) {
      // opts.channel (if any) selects a specific camera on capable devices.
      await this.client.startTalk({ channel: this.opts.channel ?? null });
      this._talkStarted = true;
    }
  }

  /**
   * Play a raw G.711 A-law (8 kHz mono) buffer.
   * @param {Buffer} alaw
   * @param {object} [opts] forwarded to DvripClient.sendAlaw (onProgress, packetSize)
   */
  async playAlaw(alaw, opts = {}) {
    await this._ensureTalk();
    return this.client.sendAlaw(alaw, opts);
  }

  /**
   * Convert an audio file/buffer with ffmpeg and play it.
   * @param {string|Buffer} input
   * @param {object} [opts] { ffmpegPath, onProgress, packetSize }
   */
  async playFile(input, opts = {}) {
    const alaw = await toAlaw(input, opts);
    return this.playAlaw(alaw, opts);
  }

  /**
   * Synthesize speech (macOS `say` / Linux `espeak`) and play it.
   * @param {string} text
   * @param {object} [opts] { voice, ffmpegPath, onProgress, packetSize }
   */
  async playText(text, opts = {}) {
    const alaw = await textToAlaw(text, opts);
    return this.playAlaw(alaw, opts);
  }

  // ---- NVR/DVR control (delegated to the underlying client) ----

  /** List the channels/cameras attached to this DVR/NVR. */
  listCameras() { return this.client.listCameras(); }
  /** Keep the session alive. */
  keepAlive() { return this.client.keepAlive(); }
  /** SystemInfo (model, firmware, serial, uptime, channel counts). */
  getSystemInfo() { return this.client.getSystemInfo(); }
  /** Per-channel live state (bitrate/record) + alarm flags. */
  getWorkState() { return this.client.getWorkState(); }
  /** Storage / HDD info. */
  getStorageInfo() { return this.client.getStorageInfo(); }
  /** Network config. */
  getNetworkConfig() { return this.client.getNetworkConfig(); }
  /** Capability flags. */
  getSystemFunction() { return this.client.getSystemFunction(); }
  /** Read any config tree by name. */
  getConfig(name, cmd) { return this.client.getConfig(name, cmd); }
  /** Write any config tree by name. */
  setConfig(name, value, cmd) { return this.client.setConfig(name, value, cmd); }
  /** Get device clock as a Date. */
  getTime() { return this.client.getTime(); }
  /** Set device clock. */
  setTime(date) { return this.client.setTime(date); }
  /** Channel titles (array). */
  getChannelTitles() { return this.client.getChannelTitles(); }
  /** Set all channel titles. */
  setChannelTitles(titles) { return this.client.setChannelTitles(titles); }
  /** Set one channel title. */
  setChannelTitle(channel, title) { return this.client.setChannelTitle(channel, title); }
  /** List users. */
  getUsers() { return this.client.getUsers(); }
  /** List groups. */
  getGroups() { return this.client.getGroups(); }
  /** Reboot the device (destructive). */
  reboot() { return this.client.reboot(); }
  /** Shut down the device (destructive). */
  shutdown() { return this.client.shutdown(); }
  /** Register/enable an IP camera on a channel. */
  addCamera(channel, cam) { return this.client.addCamera(channel, cam); }
  /** Disable the IP camera on a channel. */
  removeCamera(channel) { return this.client.removeCamera(channel); }
  /** PTZ control. */
  ptz(command, opts) { return this.client.ptz(command, opts); }
  /** Stop a continuous PTZ move. */
  ptzStop(command, opts) { return this.client.ptzStop(command, opts); }
  /** Search recorded files. */
  searchRecordings(opts) { return this.client.searchRecordings(opts); }
  /** Capture a JPEG snapshot from a channel. */
  snapshot(channel) { return this.client.snapshot(channel); }

  /** Stop the talk channel and close the socket. */
  close() {
    try {
      if (this._talkStarted) this.client.stopTalk();
    } catch (_) {
      /* ignore */
    }
    this.client.close();
  }
}

/**
 * Play the same audio on several independent devices at once.
 *
 * Use this for multiple standalone IP cameras (each a separate device with its
 * own speaker). The audio is encoded once and streamed to every target
 * concurrently — one call, shared credentials, no manual per-camera logins.
 *
 * NOTE: an NVR/DVR cannot relay talk to cameras it doesn't manage, and devices
 * with a single audio-out ignore per-channel talk. Independent cameras are
 * therefore addressed directly — that's what this helper does for you.
 *
 * @param {Array<object>} targets device options, e.g.
 *   [{ host, username, password }, ...]. Per-target fields override `defaults`.
 * @param {object} source exactly one of:
 *   { text: '...' } | { file: 'path|Buffer' } | { alaw: Buffer }
 * @param {object} [defaults] shared options merged into every target
 *   (e.g. { username, password, ffmpegPath, voice, onProgress })
 * @returns {Promise<Array<{host:string, ok:boolean, frames?:number, error?:string}>>}
 */
async function broadcast(targets, source, defaults = {}) {
  // Encode the audio a single time, then reuse for all targets.
  let alaw;
  if (source.alaw) alaw = source.alaw;
  else if (source.file != null) alaw = await toAlaw(source.file, defaults);
  else if (source.text != null) alaw = await textToAlaw(source.text, defaults);
  else throw new Error('source must provide one of: text, file, alaw');

  return Promise.all(
    targets.map(async (t) => {
      const opts = { ...defaults, ...t };
      const talk = new DvripTalk(opts);
      try {
        await talk.connect();
        const frames = await talk.playAlaw(alaw, opts);
        return { host: opts.host, ok: true, frames };
      } catch (e) {
        return { host: opts.host, ok: false, error: e.message };
      } finally {
        talk.close();
      }
    })
  );
}

module.exports = {
  DvripTalk,
  DvripClient,
  // Friendlier aliases matching the package name (same classes).
  Xmeye: DvripTalk,
  XmeyeClient: DvripClient,
  broadcast,
  sofiaHash,
  toAlaw,
  textToAlaw,
  CMD,
  RET,
};
