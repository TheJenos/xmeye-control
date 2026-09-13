'use strict';

// Minimal usage example — speak text or play an audio file through the device.
//
//   node examples/play.js <host> <user> <pass> [text|file.mp3] [channel]
//
// Examples:
//   node examples/play.js 192.168.1.2 nadun nadun1234 "Hello from Node"
//   node examples/play.js 192.168.1.2 nadun nadun1234 alert.mp3
//   node examples/play.js 192.168.1.50 admin secret "Please step back" 3
//
// `channel` is optional: leave it off to use the device's audio-out (the only
// mode most DVRs support). To talk out of one specific IP camera, point <host>
// at that camera's own IP instead — see examples/multi-camera.js.

const { DvripTalk } = require('..');

async function main() {
  const [host, user, pass, arg, channelArg] = process.argv.slice(2);
  if (!host) {
    console.error(
      'usage: node examples/play.js <host> <user> <pass> [text|file.mp3] [channel]'
    );
    process.exit(1);
  }

  const message = arg || 'Hello from the xmeye-control node package';
  const channel = channelArg != null ? parseInt(channelArg, 10) : null;

  const talk = new DvripTalk({
    host,
    username: user,
    password: pass,
    channel, // null -> device audio-out; a number -> per-channel (if supported)
  });

  const onProgress = (sent, total) =>
    process.stdout.write(`\r  streaming ${sent}/${total} frames`);

  try {
    await talk.connect();
    console.log(`connected + logged in to ${host}${channel != null ? ` (channel ${channel})` : ''}`);

    if (/\.(mp3|wav|aac|m4a|ogg|flac|aiff?)$/i.test(message)) {
      console.log('playing file:', message);
      await talk.playFile(message, { onProgress });
    } else {
      console.log('speaking:', JSON.stringify(message));
      await talk.playText(message, { onProgress });
    }
    console.log('\ndone');
  } finally {
    talk.close();
  }
}

main().catch((e) => {
  console.error('\nerror:', e.message);
  process.exit(1);
});
