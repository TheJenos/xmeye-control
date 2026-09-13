'use strict';

// Speak out of a specific camera THROUGH the NVR — one NVR login, pick a channel.
//
//   node examples/speak-channel.js <nvr-host> <user> <pass> <channel> [text|file]
//   node examples/speak-channel.js 192.168.1.2 nadun nadun1234 5 "Hello camera 5"
//
// How it works: a single DVRIP session to the NVR, then OPTalk Claim/Start/Data
// carrying that channel number. The NVR relays the G.711 audio to the IP camera
// on that channel. Requires a NETWORK camera WITH A SPEAKER on that channel —
// analog channels and empty slots have no speaker to play from.
//
// Use `list` first to see which channels are IP cameras:
//   node examples/list-cameras.js 192.168.1.2 nadun nadun1234

const { DvripTalk } = require('..');

async function main() {
  const [host, username, password, channelArg, ...rest] = process.argv.slice(2);
  if (!host || channelArg == null) {
    console.error(
      'usage: node examples/speak-channel.js <nvr-host> <user> <pass> <channel> [text|file]'
    );
    process.exit(1);
  }
  const channel = parseInt(channelArg, 10);
  const message = rest.join(' ') || `Hello from channel ${channel}`;

  // One NVR login; `channel` targets the camera on that channel.
  const talk = new DvripTalk({ host, username, password, channel });
  try {
    await talk.connect();
    console.log(`connected to NVR ${host}, talking to channel ${channel}`);

    if (/\.(mp3|wav|aac|m4a|ogg|flac|aiff?)$/i.test(message)) {
      await talk.playFile(message);
    } else {
      await talk.playText(message);
    }
    console.log('done — audio sent to channel', channel);
  } finally {
    talk.close();
  }
}

main().catch((e) => {
  console.error('error:', e.message);
  process.exit(1);
});
