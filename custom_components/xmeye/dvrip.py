"""Asyncio client for the Xiongmai DVRIP ("Sofia" / XMEye / iCSee) protocol.

Speaks just enough of the protocol on TCP 34567 to log in, read and write config
trees, drive PTZ, grab JPEG snapshots and push audio out of the device speaker
over the OPTalk backchannel.

This module is deliberately free of Home Assistant imports so it can be tested
and reused standalone.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
from datetime import datetime
import hashlib
import json
import logging
import struct
from typing import Any

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 34567
DEFAULT_TIMEOUT = 8.0

HEADER_LEN = 20

# DVRIP / Sofia command (message) IDs.
CMD_LOGIN = 1000
CMD_KEEPALIVE = 1006
CMD_CONFIG_SET = 1040
CMD_CONFIG_GET = 1042
CMD_CHANNEL_TITLE_SET = 1046
CMD_CHANNEL_TITLE_GET = 1048
CMD_SYSTEM_INFO = 1020
CMD_SYSTEM_FUNCTION = 1360
CMD_PTZ = 1400
CMD_TALK_START = 1430
CMD_TALK_DATA = 1432
CMD_TALK_CLAIM = 1434
CMD_FILE_QUERY = 1440
CMD_MACHINE = 1450
CMD_USERS = 1472
CMD_GROUPS = 1474
CMD_SNAP = 1560
CMD_ALARM_SET = 1500
CMD_ALARM_INFO = 1504
CMD_PLAYBACK_CONTROL = 1420
CMD_PLAYBACK_CLAIM = 1424

# Downloading a recording streams the whole file, which can run for minutes;
# the ordinary request timeout is sized for control commands, not this.
DOWNLOAD_TIMEOUT = 120.0

# Ret codes surfaced by the device.
RET_CODES: dict[int, str] = {
    100: "OK",
    101: "Unknown error",
    102: "Unsupported version",
    103: "Request not permitted",
    104: "User already logged in",
    105: "User is not logged in",
    106: "Username or password is incorrect",
    107: "User has no necessary permissions",
    110: "Search success, returned all files",
    111: "Search success, returned partial files",
    121: "Digital channel is not enabled",
    203: "Password is incorrect",
    205: "Account is locked (too many failed logins)",
    213: "Permission table error",
    503: "Talk channel is already open",
    504: "Talk channel is not open",
    511: "Start of upgrade",
    512: "Upgrade was not started",
    513: "Upgrade data error",
    514: "Upgrade error",
    515: "Upgrade successful",
    602: "Application restart required for change to take effect",
    603: "System (device) restart required for change to take effect",
    604: "Write file error",
    605: "Feature not supported",
    606: "Verification failed (device rejected the value/credentials)",
    607: "Config name does not exist",
    608: "Config parse error",
}

# Ret codes that mean "your credentials are wrong", not "the device is unhappy".
_AUTH_RETS = {106, 203, 205}

# The firmware default address occupying unconfigured remote-camera slots.
PLACEHOLDER_IP = "192.168.0.10"

# Addresses that mean "no camera here" rather than a reachable device.
UNSET_IPS = (PLACEHOLDER_IP, "0.0.0.0")  # noqa: S104

# 8 kHz mono G.711 A-law is what the talk backchannel expects.
ALAW_SAMPLE_RATE = 8000
ALAW_SILENCE = 0xD5
TALK_PACKET_SIZE = 320  # 40 ms at 8 kHz


class DvripError(Exception):
    """A device-level protocol error, carrying the raw Ret code when known."""

    def __init__(self, message: str, ret: int | None = None) -> None:
        """Initialise with a message and the device's Ret code."""
        super().__init__(message)
        self.ret = ret


class DvripAuthError(DvripError):
    """Authentication was rejected by the device."""


class DvripConnectionError(DvripError):
    """The device could not be reached, or the connection dropped."""


def sofia_hash(password: str = "") -> str:
    """Return the 8-character Xiongmai "Sofia" hash of ``password``.

    Reference vector: ``sofia_hash("") == "tlJwpbo6"``.
    """
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    digest = hashlib.md5(password.encode("utf-8")).digest()  # noqa: S324
    return "".join(chars[(digest[i] + digest[i + 1]) % 62] for i in range(0, 16, 2))


async def safe_call(coro: Any, default: Any) -> Any:
    """Await ``coro``, falling back to ``default`` on a device error.

    Firmware variants differ in which config trees they expose; a missing tree
    should degrade the result, not abort the whole read.
    """
    try:
        return await coro
    except DvripError as err:
        _LOGGER.debug("optional device read failed: %s", err)
        return default


def _ret_message(ret: int | None) -> str:
    """Human-readable text for a device Ret code."""
    if ret is None:
        return "no Ret code"
    return RET_CODES.get(ret, f"Ret {ret}")


class DvripClient:
    """A single authenticated DVRIP session over one TCP connection.

    The client is *not* safe for unsynchronised concurrent use: every
    request/reply exchange is serialised behind an internal lock, because the
    protocol correlates replies to requests purely by arrival order.
    """

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        username: str = "admin",
        password: str = "",
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialise the client. No I/O happens until :meth:`connect`."""
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.timeout = timeout

        self.session = 0
        self.channel_count = 0
        self.device_info: dict[str, Any] = {}

        self._seq = 0
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._waiters: list[asyncio.Future[dict[str, Any]]] = []
        self._collector: Callable[[bytes], None] | None = None
        self._collector_fail: Callable[[BaseException], None] | None = None
        self._alarm_callback: Callable[[dict[str, Any]], None] | None = None
        self._lock = asyncio.Lock()
        self._talk_open = False

    @property
    def connected(self) -> bool:
        """Whether the socket is currently open."""
        return self._writer is not None and not self._writer.is_closing()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the TCP connection and start the frame reader."""
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self.timeout
            )
        except TimeoutError as err:
            raise DvripConnectionError(
                f"timed out connecting to {self.host}:{self.port}"
            ) from err
        except OSError as err:
            raise DvripConnectionError(
                f"cannot connect to {self.host}:{self.port}: {err}"
            ) from err

        self._reader_task = asyncio.create_task(self._read_loop())

    async def close(self) -> None:
        """Stop the talk channel if open, then tear the connection down."""
        if self._talk_open:
            with contextlib.suppress(Exception):
                await self.stop_talk()

        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
            self._reader_task = None

        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()

        self._reader = None
        self._writer = None
        self.session = 0
        self._fail_waiters(DvripConnectionError("connection closed"))

    def _fail_waiters(self, err: Exception) -> None:
        """Reject every in-flight request; used when the socket dies.

        A snapshot or recording download waits on its own future via
        ``_collector``, not ``_waiters`` — without this it would sit there
        until its own timeout instead of failing the moment the connection
        actually died.
        """
        waiters, self._waiters = self._waiters, []
        for fut in waiters:
            if not fut.done():
                fut.set_exception(err)
        if self._collector_fail is not None:
            self._collector_fail(err)

    async def _read_loop(self) -> None:
        """Read DVRIP frames forever, dispatching each payload."""
        reader = self._reader
        if reader is None:
            return
        try:
            while True:
                head = await reader.readexactly(HEADER_LEN)
                if head[0] != 0xFF:
                    # Resync on a malformed stream rather than spinning.
                    _LOGGER.debug("%s: resyncing on unexpected frame", self.host)
                    continue
                self.session = struct.unpack_from("<I", head, 4)[0]
                (cmd,) = struct.unpack_from("<H", head, 14)
                (length,) = struct.unpack_from("<I", head, 16)
                payload = await reader.readexactly(length) if length else b""
                if cmd == CMD_ALARM_INFO:
                    # Pushed unprompted by the device; never route it to
                    # whatever request (or snapshot collector) happens to be
                    # waiting, or it will corrupt that reply.
                    self._handle_alarm(payload)
                elif self._collector is not None:
                    self._collector(payload)
                else:
                    self._deliver(payload)
        except asyncio.CancelledError:
            raise
        except (asyncio.IncompleteReadError, OSError) as err:
            self._fail_waiters(DvripConnectionError(f"connection lost: {err}"))
        except Exception as err:
            _LOGGER.exception("%s: reader loop failed", self.host)
            self._fail_waiters(DvripConnectionError(str(err)))

    def _deliver(self, payload: bytes) -> None:
        """Hand a JSON payload to the oldest pending request."""
        text = payload.rstrip(b"\x00\n").decode("utf-8", errors="replace")
        while self._waiters:
            fut = self._waiters.pop(0)
            if fut.done():  # timed out already — skip its stale reply
                continue
            try:
                fut.set_result(json.loads(text))
            except json.JSONDecodeError:
                fut.set_result({"_raw": text})
            return
        # Unsolicited frame (media push) with nobody waiting — drop it.

    def _handle_alarm(self, payload: bytes) -> None:
        """Parse an AlarmInfo push frame and hand it to the registered callback."""
        text = payload.rstrip(b"\x00\n").decode("utf-8", errors="replace")
        try:
            reply = json.loads(text)
        except json.JSONDecodeError:
            _LOGGER.debug("%s: malformed alarm push: %s", self.host, text)
            return
        info = reply.get("AlarmInfo")
        if not isinstance(info, dict) or self._alarm_callback is None:
            return
        try:
            self._alarm_callback(info)
        except Exception:
            _LOGGER.exception("%s: alarm callback failed", self.host)

    # ------------------------------------------------------------------
    # Framing
    # ------------------------------------------------------------------

    def _frame(self, cmd: int, payload: bytes) -> bytes:
        """Build a 20-byte DVRIP header followed by ``payload``."""
        head = bytearray(HEADER_LEN)
        head[0] = 0xFF
        struct.pack_into("<I", head, 4, self.session & 0xFFFFFFFF)
        struct.pack_into("<I", head, 8, self._seq & 0xFFFFFFFF)
        struct.pack_into("<H", head, 14, cmd)
        struct.pack_into("<I", head, 16, len(payload))
        self._seq = (self._seq + 1) & 0xFFFFFFFF
        return bytes(head) + payload

    def _write(self, cmd: int, payload: bytes) -> None:
        """Queue a raw frame on the socket without awaiting a reply."""
        if self._writer is None or self._writer.is_closing():
            raise DvripConnectionError("not connected")
        self._writer.write(self._frame(cmd, payload))

    @staticmethod
    def _encode(obj: dict[str, Any]) -> bytes:
        """Serialise a command body the way the firmware expects it."""
        return json.dumps(obj).encode("utf-8") + b"\x0a\x00"

    def _sid(self) -> str:
        """Return the session id in the 0xXXXXXXXX form the device expects."""
        return f"0x{self.session & 0xFFFFFFFF:08X}"

    async def send_json(self, cmd: int, body: dict[str, Any]) -> dict[str, Any]:
        """Send a JSON command and return the device's JSON reply."""
        async with self._lock:
            loop = asyncio.get_running_loop()
            fut: asyncio.Future[dict[str, Any]] = loop.create_future()
            self._waiters.append(fut)
            try:
                self._write(cmd, self._encode(body))
                await self._writer.drain()  # type: ignore[union-attr]
                return await asyncio.wait_for(fut, self.timeout)
            except TimeoutError as err:
                raise DvripConnectionError(
                    f"timed out waiting for a reply to command {cmd}"
                ) from err
            finally:
                if fut in self._waiters:
                    self._waiters.remove(fut)

    @staticmethod
    def _check(reply: dict[str, Any], what: str) -> dict[str, Any]:
        """Raise if the device reported a failure Ret, else return the reply."""
        ret = reply.get("Ret")
        if ret is not None and ret not in (100, 515, 602, 603):
            err_cls = DvripAuthError if ret in _AUTH_RETS else DvripError
            raise err_cls(f"{what} failed: {_ret_message(ret)}", ret)
        return reply

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    async def login(self) -> dict[str, Any]:
        """Authenticate, populating :attr:`session` and :attr:`channel_count`."""
        reply = await self.send_json(
            CMD_LOGIN,
            {
                "EncryptType": "MD5",
                "LoginType": "DVRIP-Web",
                "PassWord": sofia_hash(self.password),
                "UserName": self.username,
            },
        )
        self._check(reply, "login")
        self.session = int(str(reply.get("SessionID", "0x0")), 16)
        self.channel_count = int(reply.get("ChannelNum") or 0)
        return reply

    async def keep_alive(self) -> dict[str, Any]:
        """Refresh the session; the device drops idle sessions after ~30 s."""
        return await self.send_json(
            CMD_KEEPALIVE, {"Name": "KeepAlive", "SessionID": self._sid()}
        )

    # ------------------------------------------------------------------
    # Config trees
    # ------------------------------------------------------------------

    async def get_config(self, name: str, cmd: int = CMD_CONFIG_GET) -> Any:
        """Read a config/state tree by name, returning the value under ``name``."""
        reply = await self.send_json(cmd, {"Name": name, "SessionID": self._sid()})
        self._check(reply, f'get "{name}"')
        return reply.get(name, reply)

    async def set_config(
        self, name: str, value: Any, cmd: int = CMD_CONFIG_SET
    ) -> dict[str, Any]:
        """Write a config tree by name."""
        reply = await self.send_json(
            cmd, {"Name": name, "SessionID": self._sid(), name: value}
        )
        return self._check(reply, f'set "{name}"')

    async def system_info(self) -> dict[str, Any]:
        """Model, firmware, serial, channel counts and uptime."""
        info = await self.get_config("SystemInfo", CMD_SYSTEM_INFO)
        return info if isinstance(info, dict) else {}

    async def work_state(self) -> dict[str, Any]:
        """Per-channel live state (bitrate, recording) plus alarm flags."""
        state = await self.get_config("WorkState", CMD_SYSTEM_INFO)
        return state if isinstance(state, dict) else {}

    async def storage_info(self) -> Any:
        """Storage / HDD info, including partitions and recorded ranges."""
        return await self.get_config("StorageInfo", CMD_SYSTEM_INFO)

    async def network_config(self) -> Any:
        """Network config (IP, gateway, ports, MAC)."""
        return await self.get_config("NetWork.NetCommon")

    async def system_function(self) -> Any:
        """Device capability flags."""
        return await self.get_config("SystemFunction", CMD_SYSTEM_FUNCTION)

    async def get_time(self) -> datetime | None:
        """Read the device clock as naive wall-clock time.

        The device has no notion of a time zone, so the result is deliberately
        naive: it is whatever the recorder believes the local time to be.
        """
        reply = await self.send_json(
            1452, {"Name": "OPTimeQuery", "SessionID": self._sid()}
        )
        raw = reply.get("OPTimeQuery")
        if not raw:
            return None
        with contextlib.suppress(ValueError):
            return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")  # noqa: DTZ007
        return None

    async def set_time(self, when: datetime) -> dict[str, Any]:
        """Set the device clock. ``when`` is interpreted as device-local time."""
        return await self.send_json(
            CMD_MACHINE,
            {
                "Name": "OPTimeSetting",
                "SessionID": self._sid(),
                "OPTimeSetting": when.strftime("%Y-%m-%d %H:%M:%S"),
            },
        )

    async def channel_titles(self) -> list[str]:
        """Channel display names."""
        titles = await self.get_config("ChannelTitle", CMD_CHANNEL_TITLE_GET)
        if not isinstance(titles, list):
            return []
        return [t if isinstance(t, str) else "" for t in titles]

    async def set_channel_title(self, channel: int, title: str) -> dict[str, Any]:
        """Rename a single channel, preserving the others."""
        titles = await self.channel_titles()
        while len(titles) <= channel:
            titles.append("")
        titles[channel] = title
        return await self.send_json(
            CMD_CHANNEL_TITLE_SET,
            {
                "Name": "ChannelTitle",
                "SessionID": self._sid(),
                "ChannelTitle": titles,
            },
        )

    # ------------------------------------------------------------------
    # Channels
    # ------------------------------------------------------------------

    async def list_cameras(self) -> list[dict[str, Any]]:
        """Enumerate channels, merging remote-camera config, titles and state.

        Each configured IP camera carries the credentials the recorder stores
        for it, so a caller can open its own session to that camera. Most
        firmwares mask the password on read, so expect it to be empty and fall
        back to the recorder's own credentials.
        """
        remote = await safe_call(self.get_config("NetWork.RemoteDevice"), [])
        titles = await safe_call(self.channel_titles(), [])
        state = await safe_call(self.work_state(), {})

        remotes = remote if isinstance(remote, list) else []
        names = titles if isinstance(titles, list) else []
        states = state.get("ChannelState", []) if isinstance(state, dict) else []

        count = self.channel_count or max(len(names), len(remotes), len(states), 0)

        cameras: list[dict[str, Any]] = []
        for index in range(count):
            entry = next(
                (r for r in remotes if r.get("Channel") == index),
                remotes[index] if index < len(remotes) else {},
            )
            channel_state = states[index] if index < len(states) else {}
            bitrate = channel_state.get("Bitrate") or 0
            recording = bool(channel_state.get("Record"))
            enabled = bool(entry.get("Enable"))

            # An unconfigured remote slot still carries the firmware's factory
            # default address — that is not a real camera.
            raw_ip = entry.get("IPAddress") or ""
            real_ip = bool(raw_ip) and raw_ip not in UNSET_IPS
            configured = enabled or real_ip

            if configured:
                kind = "ip"
            elif bitrate > 0 or recording:
                kind = "analog"
            else:
                kind = "empty"

            cameras.append(
                {
                    "channel": index,
                    "title": names[index] if index < len(names) else "",
                    "type": kind,
                    "enabled": enabled,
                    "configured": configured,
                    "online": bitrate > 0 if kind == "ip" else kind == "analog",
                    "recording": recording,
                    "bitrate": bitrate,
                    "ip": raw_ip if configured else None,
                    "port": entry.get("Port") if configured else None,
                    "protocol": entry.get("Protocol") if configured else None,
                    "username": entry.get("UserName") or "" if configured else "",
                    "password": entry.get("PassWord") or "" if configured else "",
                }
            )
        return cameras

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------

    async def ptz(
        self,
        command: str,
        channel: int = 0,
        step: int = 5,
        preset: int = -1,
        stop: bool = False,
    ) -> dict[str, Any]:
        """Issue a PTZ command.

        Continuous moves run until the same command is sent with ``stop=True``.
        """
        # Starting a movement must not carry Preset: -1 — some firmware
        # families silently ignore the whole command unless a non-preset move
        # uses the sentinel value 65535 instead (the same value other DVRIP
        # clients, e.g. python-dvr's ptz_step, use to mean "no preset").
        # Stopping that same movement, however, is what actually needs -1;
        # sending 65535 there is what stopped ``ptz_stop`` from working.
        preset_field = preset if stop or "Preset" in command else 65535
        parameter = {
            "AUX": {"Number": 0, "Status": "On"},
            "Channel": channel,
            "MenuOpts": "Enter",
            "POINT": {"bottom": 0, "left": 0, "right": 0, "top": 0},
            "Pattern": "Stop" if stop else "SetBegin",
            "Preset": preset_field,
            "Step": step,
            "Tour": 1 if "Tour" in command else 0,
        }
        reply = await self.send_json(
            CMD_PTZ,
            {
                "Name": "OPPTZControl",
                "SessionID": self._sid(),
                "OPPTZControl": {"Command": command, "Parameter": parameter},
            },
        )
        return self._check(reply, f"PTZ {command}")

    def set_alarm_callback(
        self, callback: Callable[[dict[str, Any]], None] | None
    ) -> None:
        """Register (or clear, with ``None``) the AlarmInfo push handler."""
        self._alarm_callback = callback

    async def start_alarm_monitor(self) -> dict[str, Any]:
        """Ask the device to start pushing AlarmInfo events on this session.

        Not every firmware supports this — some only push alarms to a
        separately configured alarm server rather than the control session.
        """
        reply = await self.send_json(
            CMD_ALARM_SET, {"Name": "", "SessionID": self._sid()}
        )
        return self._check(reply, "alarm subscribe")

    async def reboot(self) -> dict[str, Any]:
        """Reboot the device. The connection will drop."""
        return await self.send_json(
            CMD_MACHINE,
            {
                "Name": "OPMachine",
                "SessionID": self._sid(),
                "OPMachine": {"Action": "Reboot"},
            },
        )

    async def shutdown(self) -> dict[str, Any]:
        """Power the device off. The connection will drop."""
        return await self.send_json(
            CMD_MACHINE,
            {
                "Name": "OPMachine",
                "SessionID": self._sid(),
                "OPMachine": {"Action": "Shutdown"},
            },
        )

    async def search_recordings(
        self,
        channel: int = 0,
        start: str | None = None,
        end: str | None = None,
        file_type: str = "h264",
    ) -> list[dict[str, Any]]:
        """Search recorded files; times are ``YYYY-MM-DD hh:mm:ss`` strings."""
        reply = await self.send_json(
            CMD_FILE_QUERY,
            {
                "Name": "OPFileQuery",
                "SessionID": self._sid(),
                "OPFileQuery": {
                    "BeginTime": start,
                    "EndTime": end,
                    "Channel": channel,
                    "DriverTypeMask": "0x0000FFFF",
                    "Event": "*",
                    "StreamType": "0x00000000",
                    "Type": file_type,
                },
            },
        )
        files = reply.get("OPFileQuery")
        return files if isinstance(files, list) else []

    async def download_recording(self, filename: str, start: str, end: str) -> bytes:
        """Download one recorded file's raw stream (OPPlayBack).

        ``filename``/``start``/``end`` must be the ``FileName``/``BeginTime``/
        ``EndTime`` of an entry returned by :meth:`search_recordings`. The
        device answers with its own recorded stream format (commonly raw
        H.264), not a container a browser can play directly — the caller is
        expected to remux it (see the ``media_source`` platform).
        """
        parameter = {
            "PlayMode": "ByName",
            "FileName": filename,
            "StreamType": 0,
            "Value": 0,
            "TransMode": "TCP",
        }

        async with self._lock:
            # Claim and DownloadStart must reach the device back-to-back:
            # anything else landing between them — a poll cycle's own
            # commands, most likely — has been observed to make the device
            # silently drop DownloadStart, so both go out under one lock
            # acquisition rather than one each like an ordinary command.
            loop = asyncio.get_running_loop()
            claim_fut: asyncio.Future[dict[str, Any]] = loop.create_future()
            self._waiters.append(claim_fut)
            try:
                self._write(
                    CMD_PLAYBACK_CLAIM,
                    self._encode(
                        {
                            "Name": "OPPlayBack",
                            "SessionID": self._sid(),
                            "OPPlayBack": {
                                "Action": "Claim",
                                "Parameter": parameter,
                                "StartTime": start,
                                "EndTime": end,
                            },
                        }
                    ),
                )
                await self._writer.drain()  # type: ignore[union-attr]
                claim_reply = await asyncio.wait_for(claim_fut, self.timeout)
            except TimeoutError as err:
                raise DvripConnectionError(
                    f"timed out claiming recording {filename!r} for download"
                ) from err
            finally:
                if claim_fut in self._waiters:
                    self._waiters.remove(claim_fut)
            self._check(claim_reply, "recording download claim")

            fut: asyncio.Future[bytes] = loop.create_future()
            buffer = bytearray()

            def collect(payload: bytes) -> None:
                # The device signals end-of-file with a zero-length frame.
                if fut.done():
                    return
                if not payload:
                    fut.set_result(bytes(buffer))
                    return
                buffer.extend(payload)

            def fail(err: BaseException) -> None:
                if not fut.done():
                    fut.set_exception(err)

            self._collector = collect
            self._collector_fail = fail
            try:
                self._write(
                    CMD_PLAYBACK_CONTROL,
                    self._encode(
                        {
                            "Name": "OPPlayBack",
                            "SessionID": self._sid(),
                            "OPPlayBack": {
                                "Action": "DownloadStart",
                                "Parameter": parameter,
                                "StartTime": start,
                                "EndTime": end,
                            },
                        }
                    ),
                )
                await self._writer.drain()  # type: ignore[union-attr]
                data = await asyncio.wait_for(fut, DOWNLOAD_TIMEOUT)
            except TimeoutError as err:
                raise DvripError(
                    f"timed out downloading recording {filename!r} after "
                    f"receiving {len(buffer)} bytes"
                ) from err
            finally:
                self._collector = None
                self._collector_fail = None

        with contextlib.suppress(DvripError):
            await self.send_json(
                CMD_PLAYBACK_CONTROL,
                {
                    "Name": "OPPlayBack",
                    "SessionID": self._sid(),
                    "OPPlayBack": {
                        "Action": "DownloadStop",
                        "Parameter": parameter,
                        "StartTime": start,
                        "EndTime": end,
                    },
                },
            )
        return data

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    async def snapshot(self, channel: int = 0) -> bytes:
        """Capture a single JPEG frame from ``channel``.

        The device answers OPSNAP with raw image frames rather than JSON, so the
        reader is switched into collector mode for the duration of the call.
        """
        async with self._lock:
            loop = asyncio.get_running_loop()
            fut: asyncio.Future[bytes] = loop.create_future()
            buffer = bytearray()

            def collect(payload: bytes) -> None:
                if fut.done():
                    return
                buffer.extend(payload)
                soi = buffer.find(b"\xff\xd8")
                if soi < 0:
                    return
                eoi = buffer.find(b"\xff\xd9", soi + 2)
                if eoi >= 0:
                    fut.set_result(bytes(buffer[soi : eoi + 2]))

            def fail(err: BaseException) -> None:
                if not fut.done():
                    fut.set_exception(err)

            self._collector = collect
            self._collector_fail = fail
            try:
                self._write(
                    CMD_SNAP,
                    self._encode(
                        {
                            "Name": "OPSNAP",
                            "SessionID": self._sid(),
                            "OPSNAP": {"Channel": channel},
                        }
                    ),
                )
                await self._writer.drain()  # type: ignore[union-attr]
                return await asyncio.wait_for(fut, self.timeout)
            except TimeoutError as err:
                raise DvripError(
                    f"timed out capturing a snapshot on channel {channel}"
                ) from err
            finally:
                self._collector = None
                self._collector_fail = None

    # ------------------------------------------------------------------
    # Talk backchannel
    # ------------------------------------------------------------------

    async def start_talk(self, channel: int | None = None) -> None:
        """Claim and start the audio backchannel.

        ``channel`` is ``None`` for the device's single audio-out, which is the
        only mode most Xiongmai firmwares accept. Passing a channel asks for
        per-channel routing; devices that lack it answer Ret 103.
        """
        talk: dict[str, Any] = {
            "Action": "Claim",
            "AudioFormat": {"EncodeType": "G711_ALAW"},
        }
        if channel is not None:
            talk["Channel"] = channel

        reply = await self.send_json(
            CMD_TALK_CLAIM,
            {"Name": "OPTalk", "SessionID": self._sid(), "OPTalk": talk},
        )
        ret = reply.get("Ret")
        if ret != 100:
            message = f"OPTalk claim failed: {_ret_message(ret)}"
            if channel is not None and ret == 103:
                message += (
                    " — this device does not support per-channel talk; leave the"
                    " talk channel unset, or add the camera directly by its own"
                    " IP address"
                )
            raise DvripError(message, ret)

        start: dict[str, Any] = {
            "Action": "Start",
            "AudioFormat": {"EncodeType": "G711_ALAW"},
        }
        if channel is not None:
            start["Channel"] = channel
        # Start is fire-and-forget; the device sends no reply worth awaiting.
        async with self._lock:
            self._write(
                CMD_TALK_START,
                self._encode(
                    {
                        "Name": "OPTalk",
                        "SessionID": self._sid(),
                        "OPTalk": start,
                    }
                ),
            )
            await self._writer.drain()  # type: ignore[union-attr]
        self._talk_open = True

    async def stop_talk(self) -> None:
        """Close the audio backchannel."""
        if not self.connected:
            self._talk_open = False
            return
        async with self._lock:
            with contextlib.suppress(DvripConnectionError):
                self._write(
                    CMD_TALK_START,
                    self._encode(
                        {
                            "Name": "OPTalk",
                            "SessionID": self._sid(),
                            "OPTalk": {
                                "Action": "Stop",
                                "AudioFormat": {"EncodeType": "G711_ALAW"},
                            },
                        }
                    ),
                )
                await self._writer.drain()  # type: ignore[union-attr]
        self._talk_open = False

    def _talk_header(self, packet_size: int) -> bytes:
        """Return the 8-byte media header prefixed to every audio frame."""
        header = bytearray(8)
        struct.pack_into(">I", header, 0, 0x000001FA)
        header[4] = 14  # 0x0E = G.711 A-law (0x0A would be u-law)
        header[5] = 2  # sample-rate index -> 8000 Hz
        struct.pack_into("<H", header, 6, packet_size)
        return bytes(header)

    async def send_alaw(
        self,
        alaw: bytes,
        packet_size: int = TALK_PACKET_SIZE,
        cancel: asyncio.Event | None = None,
    ) -> int:
        """Stream raw G.711 A-law samples to the speaker, paced in real time.

        Returns the number of frames sent. Call :meth:`start_talk` first.
        """
        data = bytes(alaw)
        remainder = len(data) % packet_size
        if remainder:
            data += bytes([ALAW_SILENCE]) * (packet_size - remainder)

        header = self._talk_header(packet_size)
        frame_duration = packet_size / ALAW_SAMPLE_RATE
        loop = asyncio.get_running_loop()
        started = loop.time()

        sent = 0
        for offset in range(0, len(data), packet_size):
            if cancel is not None and cancel.is_set():
                break
            async with self._lock:
                self._write(CMD_TALK_DATA, header + data[offset : offset + packet_size])
                await self._writer.drain()  # type: ignore[union-attr]
            sent += 1
            delay = started + sent * frame_duration - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
        return sent
