#!/usr/bin/env node
'use strict';

const { DvripTalk } = require('../src/index');

function parseArgs(argv) {
  const opts = { port: 34567, username: 'admin', password: '' };
  const rest = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const next = () => argv[++i];
    switch (a) {
      case '-h': case '--host': opts.host = next(); break;
      case '-p': case '--port': opts.port = parseInt(next(), 10); break;
      case '-u': case '--user': case '--username': opts.username = next(); break;
      case '-P': case '--pass': case '--password': opts.password = next(); break;
      case '-c': case '--channel': opts.channel = parseInt(next(), 10); break;
      case '--voice': opts.voice = next(); break;
      case '--ffmpeg': opts.ffmpegPath = next(); break;
      case '--help': opts.help = true; break;
      default: rest.push(a);
    }
  }
  opts._ = rest;
  return opts;
}

const USAGE = `xmeye-control — control Xiongmai/XMEye (DVRIP) NVRs, DVRs & IP cameras

Usage:
  xmeye-control --host <ip> --user <u> --pass <pw> <command> [args]

Control commands:
  list                         list channels / attached cameras
  info                         print SystemInfo (model, firmware, serial)
  time                         print device clock
  users                        list device accounts
  snapshot [ch] [out.jpg]      save a JPEG snapshot from a channel
  recordings "<start>" "<end>" search recordings (YYYY-MM-DD hh:mm:ss; -c channel)
  ptz <command> [preset]       PTZ move (DirectionUp/…, GotoPreset, etc.; -c channel)
  settitle -c <n> <title>      set a channel's title
  reboot                       reboot the device

Audio commands:
  play  <file>                 play an audio file through the speaker
  say   <text...>              speak text (TTS) through the speaker
  alaw  <raw-g711a-file>       stream a raw G.711 A-law file

Options:
  -h, --host      device IP/hostname            (required)
  -p, --port      DVRIP port                    (default 34567)
  -u, --user      username                      (default admin)
  -P, --pass      password                      (default empty)
  -c, --channel   target camera/channel index   (default: device audio-out)
                  NOTE: most DVRs/NVRs only have one talk-out and ignore this;
                  to drive one IP camera, point --host at that camera's IP.
      --voice     TTS voice for 'say'           (optional)
      --ffmpeg    path to ffmpeg binary         (default ffmpeg)
      --help      show this help

Examples:
  xmeye-control -h 192.168.1.2 -u nadun -P nadun1234 list
  xmeye-control -h 192.168.1.2 -u nadun -P nadun1234 snapshot 0 cam0.jpg
  xmeye-control -h 192.168.1.2 -u nadun -P nadun1234 recordings "2026-09-11 00:00:00" "2026-09-12 00:00:00"
  xmeye-control -h 192.168.1.2 -u nadun -P nadun1234 -c 1 ptz DirectionUp
  xmeye-control -h 192.168.1.2 -u nadun -P nadun1234 say "Hello there"
`;

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  if (opts.help || opts._.length === 0) {
    process.stdout.write(USAGE);
    process.exit(opts.help ? 0 : 1);
  }
  if (!opts.host) {
    console.error('error: --host is required\n');
    process.stdout.write(USAGE);
    process.exit(1);
  }

  const [cmd, ...args] = opts._;
  const talk = new DvripTalk(opts);

  const progress = (sent, total) => {
    const pct = Math.round((sent / total) * 100);
    process.stderr.write(`\r  streaming ${sent}/${total} frames (${pct}%)`);
    if (sent === total) process.stderr.write('\n');
  };

  try {
    process.stderr.write(`connecting to ${opts.host}:${opts.port} ...\n`);
    await talk.connect();
    process.stderr.write(`logged in as ${opts.username}\n`);

    if (cmd === 'list') {
      const cams = await talk.listCameras();
      const rows = cams.map((c) => ({
        CH: c.channel,
        TYPE: c.type,
        TITLE: c.title || '-',
        STATUS: c.type === 'empty' ? '-' : c.online ? 'online' : 'offline',
        REC: c.recording ? 'yes' : 'no',
        KBPS: c.bitrate || '-',
        ADDRESS: c.ip ? `${c.ip}:${c.port} (${c.username})` : '-',
      }));
      if (rows.length === 0) {
        process.stderr.write('no channels reported\n');
      } else {
        // simple aligned table on stdout
        const cols = Object.keys(rows[0]);
        const w = {};
        cols.forEach((k) => {
          w[k] = Math.max(k.length, ...rows.map((r) => String(r[k]).length));
        });
        const line = (vals) => cols.map((k) => String(vals[k]).padEnd(w[k])).join('  ');
        const header = {};
        cols.forEach((k) => (header[k] = k));
        console.log(line(header));
        rows.forEach((r) => console.log(line(r)));
      }
    } else if (cmd === 'play') {
      if (!args[0]) throw new Error('play requires a file path');
      await talk.playFile(args[0], { onProgress: progress });
    } else if (cmd === 'say') {
      if (args.length === 0) throw new Error('say requires text');
      await talk.playText(args.join(' '), { voice: opts.voice, onProgress: progress });
    } else if (cmd === 'alaw') {
      if (!args[0]) throw new Error('alaw requires a raw g711a file path');
      const buf = require('fs').readFileSync(args[0]);
      await talk.playAlaw(buf, { onProgress: progress });
    } else if (cmd === 'info') {
      const info = await talk.getSystemInfo();
      console.log(JSON.stringify(info, null, 2));
    } else if (cmd === 'time') {
      const t = await talk.getTime();
      console.log(t ? t.toISOString() : 'unknown');
    } else if (cmd === 'users') {
      const users = await talk.getUsers();
      (Array.isArray(users) ? users : []).forEach((u) =>
        console.log(`${u.Name}\t(${u.Group})\t${u.Memo || ''}`)
      );
    } else if (cmd === 'snapshot') {
      const ch = parseInt(args[0] != null ? args[0] : opts.channel || 0, 10);
      const out = args[1] || `snapshot-ch${ch}.jpg`;
      const jpeg = await talk.snapshot(ch);
      require('fs').writeFileSync(out, jpeg);
      console.log(`saved ${jpeg.length} bytes -> ${out}`);
    } else if (cmd === 'recordings') {
      const [start, end] = args;
      if (!start || !end)
        throw new Error('recordings requires "<start>" "<end>" (YYYY-MM-DD hh:mm:ss)');
      const ch = opts.channel || 0;
      const files = await talk.searchRecordings({ channel: ch, start, end });
      console.log(`${files.length} file(s) on channel ${ch}:`);
      files.forEach((f) => console.log(`  ${f.BeginTime} - ${f.EndTime}  ${f.FileName}`));
    } else if (cmd === 'ptz') {
      const command = args[0];
      if (!command) throw new Error('ptz requires a command (e.g. DirectionUp, GotoPreset)');
      const ch = opts.channel || 0;
      await talk.ptz(command, { channel: ch, preset: args[1] != null ? parseInt(args[1], 10) : -1 });
      console.log(`ptz ${command} sent to channel ${ch}`);
    } else if (cmd === 'settitle') {
      const ch = opts.channel;
      if (ch == null || !args[0])
        throw new Error('settitle requires --channel <n> and a title');
      await talk.setChannelTitle(ch, args.join(' '));
      console.log(`channel ${ch} title set`);
    } else if (cmd === 'reboot') {
      await talk.reboot();
      console.log('reboot command sent');
    } else {
      throw new Error(`unknown command: ${cmd}`);
    }

    process.stderr.write('done.\n');
  } catch (e) {
    process.stderr.write(`\nerror: ${e.message}\n`);
    process.exitCode = 1;
  } finally {
    talk.close();
  }
}

main();
