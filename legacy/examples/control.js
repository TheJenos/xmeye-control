'use strict';

// NVR control tour — read-only queries + a snapshot.
//
//   node examples/control.js <host> <user> <pass>

const fs = require('fs');
const { DvripTalk } = require('..');

async function main() {
  const [host, username, password] = process.argv.slice(2);
  if (!host) {
    console.error('usage: node examples/control.js <host> <user> <pass>');
    process.exit(1);
  }

  const nvr = new DvripTalk({ host, username, password });
  await nvr.connect();

  const info = await nvr.getSystemInfo();
  console.log(`Model:    ${info.HardWare}`);
  console.log(`Firmware: ${info.SoftWareVersion}`);
  console.log(`Serial:   ${info.SerialNo}`);
  console.log(`Channels: ${info.DigChannel}`);

  console.log(`Time:     ${(await nvr.getTime())?.toISOString()}`);

  const cams = await nvr.listCameras();
  console.log(`\nChannels (${cams.length}):`);
  cams.forEach((c) =>
    console.log(
      `  CH${c.channel} ${c.type.padEnd(6)} ${c.online ? 'online ' : 'offline'} ` +
        `${c.recording ? 'REC' : '   '} ${c.title || ''}`
    )
  );

  // Snapshot from the first online channel.
  const live = cams.find((c) => c.online);
  if (live) {
    const jpeg = await nvr.snapshot(live.channel);
    const out = `snapshot-ch${live.channel}.jpg`;
    fs.writeFileSync(out, jpeg);
    console.log(`\nSnapshot ch${live.channel}: ${jpeg.length} bytes -> ${out}`);
  }

  // Example writes (commented out — uncomment to use):
  // await nvr.setChannelTitle(0, 'Front Door');
  // await nvr.ptz('DirectionUp', { channel: 1, step: 5 });
  // await nvr.addCamera(5, { ip: '192.168.1.50', username: 'admin', password: 'pw' });
  // await nvr.setTime(new Date());
  // await nvr.reboot();

  nvr.close();
}

main().catch((e) => {
  console.error('error:', e.message);
  process.exit(1);
});
