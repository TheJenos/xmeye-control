'use strict';

const { spawn } = require('child_process');
const os = require('os');
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');

/**
 * Convert any ffmpeg-readable audio file (or stdin buffer) to raw G.711 A-law,
 * 8 kHz, mono — the format DVRIP expects for talk audio.
 *
 * Requires `ffmpeg` on PATH (override with opts.ffmpegPath).
 *
 * @param {string|Buffer} input path to a file, or a Buffer of encoded audio
 * @param {object} [opts]
 * @param {string} [opts.ffmpegPath='ffmpeg']
 * @returns {Promise<Buffer>} raw a-law samples
 */
function toAlaw(input, opts = {}) {
  const ffmpeg = opts.ffmpegPath || 'ffmpeg';
  const inArg = Buffer.isBuffer(input) ? 'pipe:0' : input;
  const args = [
    '-hide_banner',
    '-loglevel', 'error',
    '-i', inArg,
    '-ar', '8000',
    '-ac', '1',
    '-f', 'alaw',
    'pipe:1',
  ];

  return new Promise((resolve, reject) => {
    const proc = spawn(ffmpeg, args);
    const out = [];
    const err = [];
    proc.stdout.on('data', (d) => out.push(d));
    proc.stderr.on('data', (d) => err.push(d));
    proc.on('error', (e) =>
      reject(new Error(`failed to run ffmpeg (${ffmpeg}): ${e.message}`))
    );
    proc.on('close', (code) => {
      if (code === 0) return resolve(Buffer.concat(out));
      reject(
        new Error(
          `ffmpeg exited with code ${code}: ${Buffer.concat(err).toString().trim()}`
        )
      );
    });
    if (Buffer.isBuffer(input)) {
      proc.stdin.write(input);
      proc.stdin.end();
    }
  });
}

/**
 * Synthesize speech to a G.711 A-law buffer.
 * macOS: uses the built-in `say`. Linux: uses `espeak-ng`/`espeak` if present.
 *
 * @param {string} text
 * @param {object} [opts]
 * @param {string} [opts.voice] voice name for `say`/`espeak`
 * @param {string} [opts.ffmpegPath]
 * @returns {Promise<Buffer>} raw a-law samples
 */
async function textToAlaw(text, opts = {}) {
  const tmp = path.join(
    os.tmpdir(),
    `dvrip-tts-${crypto.randomBytes(6).toString('hex')}.wav`
  );
  try {
    if (process.platform === 'darwin') {
      await run('say', [
        ...(opts.voice ? ['-v', opts.voice] : []),
        '-o', tmp,
        '--data-format=LEI16@22050',
        text,
      ]);
    } else {
      const espeak = (await which('espeak-ng')) ? 'espeak-ng' : 'espeak';
      await run(espeak, [
        ...(opts.voice ? ['-v', opts.voice] : []),
        '-w', tmp,
        text,
      ]);
    }
    return await toAlaw(tmp, opts);
  } finally {
    fs.promises.unlink(tmp).catch(() => {});
  }
}

function run(cmd, args) {
  return new Promise((resolve, reject) => {
    const p = spawn(cmd, args);
    const err = [];
    p.stderr.on('data', (d) => err.push(d));
    p.on('error', (e) =>
      reject(new Error(`failed to run ${cmd}: ${e.message}`))
    );
    p.on('close', (code) =>
      code === 0
        ? resolve()
        : reject(new Error(`${cmd} exited ${code}: ${Buffer.concat(err)}`))
    );
  });
}

function which(cmd) {
  return new Promise((resolve) => {
    const p = spawn('sh', ['-c', `command -v ${cmd}`]);
    p.on('error', () => resolve(false));
    p.on('close', (code) => resolve(code === 0));
  });
}

module.exports = { toAlaw, textToAlaw };
