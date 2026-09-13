'use strict';

// List all channels/cameras attached to a DVR/NVR.
//
//   node examples/list-cameras.js <host> <user> <pass>
//   node examples/list-cameras.js 192.168.1.2 nadun nadun1234

const { DvripTalk } = require('..');

async function main() {
  const [host, username, password] = process.argv.slice(2);
  if (!host) {
    console.error('usage: node examples/list-cameras.js <host> <user> <pass>');
    process.exit(1);
  }

  const dvr = new DvripTalk({ host, username, password });
  await dvr.connect();

  const cams = await dvr.listCameras();
  dvr.close();

  console.log(`${host} — ${cams.length} channels:\n`);
  for (const c of cams) {
    const where = c.ip ? `${c.ip}:${c.port}` : 'local/analog';
    const status =
      c.type === 'empty' ? 'empty' : c.online ? 'online' : 'offline';
    console.log(
      `  CH${c.channel}  ${c.type.padEnd(6)} ${status.padEnd(7)} ` +
        `${c.recording ? 'REC ' : '    '}${String(c.bitrate).padStart(5)}kbps  ` +
        `${(c.title || '-').padEnd(10)} ${where} ${c.username}:${c.password}`
    );
  }

  // Handy: the IP cameras you could talk to directly
  const ipCams = cams.filter((c) => c.type === 'ip' && c.online);
  if (ipCams.length) {
    console.log('\nIP cameras you can talk to directly:');
    ipCams.forEach((c) => console.log(`  ${c.ip}  (${c.username})`));
  }
}

main().catch((e) => {
  console.error('error:', e.message);
  process.exit(1);
});
