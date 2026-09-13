"""Tests for the DVRIP protocol client, driven by a fake device."""

from __future__ import annotations

import asyncio
import json
import struct
from typing import Any

import pytest
from xmeye_dvrip import (
    CMD_LOGIN,
    CMD_SNAP,
    CMD_TALK_DATA,
    HEADER_LEN,
    DvripAuthError,
    DvripClient,
    DvripError,
    sofia_hash,
)


def frame(cmd: int, payload: bytes, session: int = 0) -> bytes:
    """Build a DVRIP frame the way a device would."""
    head = bytearray(HEADER_LEN)
    head[0] = 0xFF
    struct.pack_into("<I", head, 4, session)
    struct.pack_into("<H", head, 14, cmd)
    struct.pack_into("<I", head, 16, len(payload))
    return bytes(head) + payload


def json_frame(cmd: int, body: dict[str, Any]) -> bytes:
    """Build a JSON reply frame."""
    return frame(cmd, json.dumps(body).encode() + b"\x0a\x00")


class FakeDevice:
    """A minimal DVRIP server that records what it was sent."""

    def __init__(self, *, login_ret: int = 100, talk_claim_ret: int = 100) -> None:
        """Initialise the fake device."""
        self.login_ret = login_ret
        self.talk_claim_ret = talk_claim_ret
        self.received: list[tuple[int, bytes]] = []
        self.server: asyncio.Server | None = None
        self.port = 0

    async def start(self) -> None:
        """Listen on an ephemeral loopback port."""
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        """Shut the listener down."""
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Serve one client until it disconnects."""
        try:
            while True:
                head = await reader.readexactly(HEADER_LEN)
                (length,) = struct.unpack_from("<I", head, 16)
                (cmd,) = struct.unpack_from("<H", head, 14)
                payload = await reader.readexactly(length) if length else b""
                self.received.append((cmd, payload))
                reply = self._reply(cmd, payload)
                if reply:
                    writer.write(reply)
                    await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass

    def _reply(self, cmd: int, payload: bytes) -> bytes:
        """Produce the device's answer to one command."""
        if cmd == CMD_LOGIN:
            return json_frame(
                cmd + 1,
                {
                    "Ret": self.login_ret,
                    "SessionID": "0x0000ABCD",
                    "ChannelNum": 2,
                },
            )
        if cmd == CMD_SNAP:
            # Real devices dribble the JPEG across several frames.
            return frame(cmd + 1, b"\xff\xd8junk") + frame(cmd + 1, b"more\xff\xd9")
        if cmd == CMD_TALK_DATA:
            return b""  # audio frames are never acknowledged
        body = json.loads(payload.rstrip(b"\x00\n"))
        name = body.get("Name", "")
        if name == "OPTalk":
            if body["OPTalk"]["Action"] == "Claim":
                return json_frame(
                    cmd + 1, {"Ret": self.talk_claim_ret, "Name": "OPTalk"}
                )
            return b""
        if name == "ChannelTitle":
            return json_frame(cmd + 1, {"Ret": 100, "ChannelTitle": ["Front", "Back"]})
        if name == "NetWork.RemoteDevice":
            return json_frame(
                cmd + 1,
                {
                    "Ret": 100,
                    "NetWork.RemoteDevice": [
                        {
                            "Channel": 0,
                            "Enable": True,
                            "IPAddress": "192.168.1.50",
                            "Port": 34567,
                            "UserName": "camuser",
                            "PassWord": "",
                        },
                        {
                            "Channel": 1,
                            "Enable": False,
                            "IPAddress": "192.168.0.10",
                        },
                    ],
                },
            )
        if name == "WorkState":
            return json_frame(
                cmd + 1,
                {
                    "Ret": 100,
                    "WorkState": {
                        "ChannelState": [
                            {"Bitrate": 1024, "Record": True},
                            {"Bitrate": 0, "Record": False},
                        ]
                    },
                },
            )
        if name == "Missing":
            return json_frame(cmd + 1, {"Ret": 607, "Name": "Missing"})
        return json_frame(cmd + 1, {"Ret": 100, "Name": name})


@pytest.fixture
async def device():
    """Run a fake device for the duration of a test."""
    dev = FakeDevice()
    await dev.start()
    yield dev
    await dev.stop()


async def connect(dev: FakeDevice) -> DvripClient:
    """Return a logged-in client talking to ``dev``."""
    client = DvripClient(host="127.0.0.1", port=dev.port, password="secret")
    await client.connect()
    await client.login()
    return client


def test_sofia_hash_reference_vector() -> None:
    """The empty password must hash to the documented value."""
    assert sofia_hash("") == "tlJwpbo6"
    assert len(sofia_hash("anything")) == 8


async def test_login_sets_session_and_channels(device: FakeDevice) -> None:
    """A successful login records the session id and channel count."""
    client = await connect(device)
    try:
        assert client.session == 0xABCD
        assert client.channel_count == 2
        cmd, payload = device.received[0]
        assert cmd == CMD_LOGIN
        body = json.loads(payload.rstrip(b"\x00\n"))
        assert body["PassWord"] == sofia_hash("secret")
        assert body["UserName"] == "admin"
    finally:
        await client.close()


async def test_login_failure_raises_auth_error() -> None:
    """Ret 106 is surfaced as an authentication error."""
    dev = FakeDevice(login_ret=106)
    await dev.start()
    client = DvripClient(host="127.0.0.1", port=dev.port)
    try:
        await client.connect()
        with pytest.raises(DvripAuthError) as excinfo:
            await client.login()
        assert excinfo.value.ret == 106
    finally:
        await client.close()
        await dev.stop()


async def test_get_config_error_ret_raises(device: FakeDevice) -> None:
    """A non-OK Ret on a config read becomes a DvripError."""
    client = await connect(device)
    try:
        with pytest.raises(DvripError) as excinfo:
            await client.get_config("Missing")
        assert excinfo.value.ret == 607
    finally:
        await client.close()


async def test_list_cameras_merges_sources(device: FakeDevice) -> None:
    """Channel list merges remote config, titles and live state."""
    client = await connect(device)
    try:
        cameras = await client.list_cameras()
    finally:
        await client.close()

    assert len(cameras) == 2
    first, second = cameras
    assert first["title"] == "Front"
    assert first["type"] == "ip"
    assert first["online"] is True
    assert first["recording"] is True
    assert first["bitrate"] == 1024
    # Credentials come through so a caller can open a direct camera session.
    assert first["ip"] == "192.168.1.50"
    assert first["port"] == 34567
    assert first["username"] == "camuser"
    # The placeholder address must not be reported as a configured camera.
    assert second["configured"] is False
    assert second["ip"] is None
    assert second["type"] == "empty"
    assert second["username"] == ""


async def test_snapshot_reassembles_across_frames(device: FakeDevice) -> None:
    """A JPEG split over several frames is returned whole."""
    client = await connect(device)
    try:
        image = await client.snapshot(0)
    finally:
        await client.close()
    assert image.startswith(b"\xff\xd8")
    assert image.endswith(b"\xff\xd9")
    assert image == b"\xff\xd8junkmore\xff\xd9"


async def test_snapshot_leaves_client_usable(device: FakeDevice) -> None:
    """Collector mode is torn down so JSON commands still work afterwards."""
    client = await connect(device)
    try:
        await client.snapshot(0)
        titles = await client.channel_titles()
        assert titles == ["Front", "Back"]
    finally:
        await client.close()


async def test_send_alaw_pads_and_frames_audio(device: FakeDevice) -> None:
    """Audio is padded to whole frames and prefixed with the media header."""
    client = await connect(device)
    try:
        await client.start_talk()
        device.received.clear()
        # 500 bytes pads up to two 320-byte frames.
        sent = await client.send_alaw(b"\x55" * 500, packet_size=320)
    finally:
        await client.close()

    assert sent == 2
    audio = [p for cmd, p in device.received if cmd == CMD_TALK_DATA]
    assert len(audio) == 2
    for packet in audio:
        assert len(packet) == 328  # 8-byte media header + 320 samples
        assert packet[:4] == b"\x00\x00\x01\xfa"
        assert packet[4] == 14  # G.711 A-law
        assert packet[5] == 2  # 8 kHz
        assert struct.unpack_from("<H", packet, 6)[0] == 320
    # The tail of the last frame is A-law silence, not zeroes.
    assert audio[1].endswith(b"\xd5")


async def test_send_alaw_honours_cancellation(device: FakeDevice) -> None:
    """Setting the cancel event stops the stream early."""
    client = await connect(device)
    cancel = asyncio.Event()
    cancel.set()
    try:
        await client.start_talk()
        sent = await client.send_alaw(b"\x55" * 3200, cancel=cancel)
    finally:
        await client.close()
    assert sent == 0


async def test_start_talk_defaults_to_device_audio_out(device: FakeDevice) -> None:
    """Without a channel, the claim carries no Channel field."""
    client = await connect(device)
    try:
        device.received.clear()
        await client.start_talk()
    finally:
        await client.close()
    claim = json.loads(device.received[0][1].rstrip(b"\x00\n"))
    assert claim["OPTalk"]["Action"] == "Claim"
    assert "Channel" not in claim["OPTalk"]


async def test_start_talk_with_channel_targets_one_camera(
    device: FakeDevice,
) -> None:
    """Per-channel talk puts the channel in both the claim and the start."""
    client = await connect(device)
    try:
        device.received.clear()
        await client.start_talk(channel=1)
    finally:
        await client.close()

    claim, start = (json.loads(p.rstrip(b"\x00\n")) for _, p in device.received[:2])
    assert claim["OPTalk"]["Channel"] == 1
    assert claim["OPTalk"]["AudioFormat"]["EncodeType"] == "G711_ALAW"
    assert start["OPTalk"]["Action"] == "Start"
    assert start["OPTalk"]["Channel"] == 1


async def test_per_channel_talk_rejection_explains_itself() -> None:
    """Ret 103 on a channel claim points the user at the direct route."""
    dev = FakeDevice(talk_claim_ret=103)
    await dev.start()
    client = DvripClient(host="127.0.0.1", port=dev.port)
    try:
        await client.connect()
        await client.login()
        with pytest.raises(DvripError) as excinfo:
            await client.start_talk(channel=1)
        assert excinfo.value.ret == 103
        assert "does not support per-channel talk" in str(excinfo.value)
    finally:
        await client.close()
        await dev.stop()


async def test_ptz_sends_expected_body(device: FakeDevice) -> None:
    """PTZ moves carry the channel, step and stop flag the device expects."""
    client = await connect(device)
    try:
        device.received.clear()
        await client.ptz("DirectionLeft", channel=1, step=3)
        await client.ptz("DirectionLeft", channel=1, step=3, stop=True)
    finally:
        await client.close()

    bodies = [json.loads(p.rstrip(b"\x00\n")) for _, p in device.received]
    move, stop = bodies
    assert move["OPPTZControl"]["Command"] == "DirectionLeft"
    assert move["OPPTZControl"]["Parameter"]["Channel"] == 1
    assert move["OPPTZControl"]["Parameter"]["Step"] == 3
    assert move["OPPTZControl"]["Parameter"]["Pattern"] == "SetBegin"
    assert stop["OPPTZControl"]["Parameter"]["Pattern"] == "Stop"


async def test_session_id_is_formatted_for_the_device(device: FakeDevice) -> None:
    """Commands carry the session id in the device's 0xXXXXXXXX form."""
    client = await connect(device)
    try:
        device.received.clear()
        await client.keep_alive()
    finally:
        await client.close()
    body = json.loads(device.received[0][1].rstrip(b"\x00\n"))
    assert body["SessionID"] == "0x0000ABCD"
