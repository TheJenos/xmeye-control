# Legacy Node.js library

This is the original `xmeye-control` npm package: a Node.js client and CLI for
the Xiongmai DVRIP ("Sofia") protocol.

**It is no longer the point of this repository.** The project is now a Home
Assistant custom integration, which lives in
[`../custom_components/xmeye/`](../custom_components/xmeye/) and is a from-scratch
asyncio rewrite in Python.

This directory is kept as a protocol reference — the command ids, Ret codes,
Sofia hash and OPTalk framing here are the source material the Python client was
ported from. It is not maintained, not published, and not loaded by Home
Assistant.

```
src/client.js   DVRIP framing, login, config get/set, PTZ, snapshot, talk
src/sofia.js    the password hash
src/audio.js    ffmpeg / TTS helpers for producing G.711 A-law
bin/cli.js      command-line interface
examples/       usage samples
```
