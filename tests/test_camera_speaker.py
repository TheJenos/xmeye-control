"""Tests for per-camera speaker routing.

These need Home Assistant importable, so they skip when it is not installed.
The protocol tests in ``test_dvrip.py`` cover the wire format without it.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("homeassistant")

from custom_components.xmeye.const import (
    ROUTE_CHANNEL,
    ROUTE_DIRECT,
)
from custom_components.xmeye.dvrip import DvripError
from custom_components.xmeye.media_player import (
    XmeyeCameraSpeaker,
)
from homeassistant.exceptions import HomeAssistantError

IP_CAMERA = {
    "channel": 0,
    "title": "Driveway",
    "type": "ip",
    "online": True,
    "ip": "192.168.1.50",
    "port": 34567,
    "username": "camuser",
    "password": "",
}

ANALOG_CHANNEL = {
    "channel": 1,
    "title": "Garage",
    "type": "analog",
    "online": True,
    "ip": None,
    "port": None,
    "username": "",
    "password": "",
}


class FakeEntry:
    entry_id = "entry123"
    data = {"username": "admin", "password": "nvrpass"}
    options: dict[str, Any] = {}


class FakeData:
    system_info: dict[str, Any] = {}

    def __init__(self, cameras: list[dict[str, Any]]) -> None:
        self.cameras = cameras

    def camera(self, channel: int) -> dict[str, Any]:
        for cam in self.cameras:
            if cam["channel"] == channel:
                return cam
        return {}


class FakeCoordinator:
    host = "192.168.1.2"
    device_name = "NVR"

    def __init__(self, cameras: list[dict[str, Any]]) -> None:
        self.config_entry = FakeEntry()
        self.data = FakeData(cameras)
        self.recorder_client = object()
        self.ensure_calls = 0

    async def async_ensure_connected(self) -> Any:
        self.ensure_calls += 1
        return self.recorder_client


def make_speaker(channel: int = 0) -> XmeyeCameraSpeaker:
    """Build a camera speaker wired to a fake coordinator."""
    coordinator = FakeCoordinator([IP_CAMERA, ANALOG_CHANNEL])
    return XmeyeCameraSpeaker(coordinator, channel)


def test_ip_camera_prefers_the_direct_route() -> None:
    speaker = make_speaker(0)
    assert speaker._candidate_routes() == [ROUTE_DIRECT, ROUTE_CHANNEL]


def test_channel_without_an_address_can_only_use_the_recorder() -> None:
    speaker = make_speaker(1)
    assert speaker._candidate_routes() == [ROUTE_CHANNEL]


def test_remembered_route_is_tried_first() -> None:
    speaker = make_speaker(0)
    speaker._route = ROUTE_CHANNEL
    assert speaker._candidate_routes() == [ROUTE_CHANNEL, ROUTE_DIRECT]


async def test_falls_back_to_the_recorder_when_direct_fails() -> None:
    """A camera that refuses a direct session is reached via the recorder."""
    speaker = make_speaker(0)
    attempts: list[str] = []

    async def direct(alaw: bytes) -> None:
        attempts.append(ROUTE_DIRECT)
        raise DvripError("cannot connect to 192.168.1.50:34567")

    async def recorder(alaw: bytes) -> None:
        attempts.append(ROUTE_CHANNEL)

    speaker._async_deliver_direct = direct
    speaker._async_deliver_via_recorder = recorder

    await speaker._async_deliver(b"\x55" * 320)

    assert attempts == [ROUTE_DIRECT, ROUTE_CHANNEL]
    # The working route is remembered, so the next play skips the dead one.
    assert speaker._route == ROUTE_CHANNEL

    attempts.clear()
    await speaker._async_deliver(b"\x55" * 320)
    assert attempts == [ROUTE_CHANNEL]


async def test_a_dead_remembered_route_is_forgotten() -> None:
    """When the cached route stops working, the other one is tried and kept."""
    speaker = make_speaker(0)
    speaker._route = ROUTE_CHANNEL

    async def direct(alaw: bytes) -> None:
        return None

    async def recorder(alaw: bytes) -> None:
        raise DvripError("OPTalk claim failed: Request not permitted", 103)

    speaker._async_deliver_direct = direct
    speaker._async_deliver_via_recorder = recorder

    await speaker._async_deliver(b"\x55" * 320)
    assert speaker._route == ROUTE_DIRECT


async def test_error_names_every_route_that_was_tried() -> None:
    """If nothing works the user gets told what was attempted and why."""
    speaker = make_speaker(0)

    async def direct(alaw: bytes) -> None:
        raise DvripError("login failed: Username or password is incorrect", 106)

    async def recorder(alaw: bytes) -> None:
        raise DvripError("OPTalk claim failed: Request not permitted", 103)

    speaker._async_deliver_direct = direct
    speaker._async_deliver_via_recorder = recorder

    with pytest.raises(HomeAssistantError) as excinfo:
        await speaker._async_deliver(b"\x55" * 320)

    message = str(excinfo.value)
    assert "Driveway" in message
    assert "direct: login failed" in message
    assert "channel: OPTalk claim failed" in message
    assert speaker._route is None


async def test_direct_route_uses_camera_then_recorder_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The camera's own username is used; the password falls back to the NVR's."""
    speaker = make_speaker(0)
    built: dict[str, Any] = {}

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            built.update(kwargs)

        async def connect(self) -> None: ...
        async def login(self) -> None: ...
        async def start_talk(self, channel: int | None = None) -> None:
            built["talk_channel"] = channel

        async def send_alaw(self, alaw: bytes, cancel: Any = None) -> int:
            built["sent"] = len(alaw)
            return 1

        async def stop_talk(self) -> None: ...
        async def close(self) -> None:
            built["closed"] = True

    monkeypatch.setattr("custom_components.xmeye.media_player.DvripClient", FakeClient)
    await speaker._async_deliver_direct(b"\x55" * 320)

    assert built["host"] == "192.168.1.50"
    assert built["port"] == 34567
    assert built["username"] == "camuser"  # from the recorder's camera table
    assert built["password"] == "nvrpass"  # masked on read, so the NVR's is used
    # A direct session talks to the camera's own audio-out, not a channel.
    assert built["talk_channel"] is None
    assert built["sent"] == 320
    assert built["closed"] is True
