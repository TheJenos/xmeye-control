'use strict';

// Broadcast one message to several cameras with a SINGLE call and shared
// credentials — no manual per-camera logins.
//
//   node examples/multi-camera.js "Please leave the area"
//
// Why not "through the NVR"? On these systems talk-back is per-DEVICE:
//   * A DVR/NVR has ONE audio-out and ignores per-channel talk — every OPTalk
//     goes to that single output regardless of any Channel field.
//   * An NVR can only relay to cameras it manages; standalone IP cameras are
//     separate devices. Each camera with a speaker runs DVRIP on its own IP.
// So independent cameras are addressed directly. `broadcast()` does that fan-out
// for you: encode once, stream to all targets concurrently.

const { broadcast } = require('..');

// Credentials shared by all cameras go in `defaults`; per-camera overrides in
// the target list. (These are separate devices, not NVR channels.)
const CAMERAS = [
  { host: '192.168.1.5' },
  { host: '192.168.1.6' },
];

async function main() {
  const text = process.argv.slice(2).join(' ') || 'Attention please';

  const results = await broadcast(
    CAMERAS,
    { text }, // or { file: 'siren.mp3' } or { alaw: buffer }
    { username: 'admin', password: 'nadun1234' } // shared defaults
  );

  for (const r of results) {
    console.log(r.ok ? `  ✓ ${r.host} (${r.frames} frames)` : `  ✗ ${r.host}: ${r.error}`);
  }
}

main().catch((e) => {
  console.error('error:', e.message);
  process.exit(1);
});
