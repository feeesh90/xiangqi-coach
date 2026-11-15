"""Real loopback TCP + game/policy tests; no firewall changes or LAN listener.

Only the interface provider is replaced for local transport tests. Production
entry points still reject loopback, public profiles, disabled firewalls and VPNs.
"""
import copy
from dataclasses import dataclass
import queue
import random
import socket
import struct
import time

import pytest

import lan_network
import lan_client
from lan_network import Interface
from lan_protocol import (Channel, MAX_FRAME, ProtocolError, StateVerifier,
                          close_socket, receive_frame, send_frame)
from lan_server import LanServer, Room
from lan_client import LanClient
from rules import Position, Move, legal_moves
from repetition import advance, playable_moves


def command(room, kind, **values):
    return {"type": kind, "game": room.game, "revision": room.revision, **values}


def continuation(position, context, rng):
    choices = []
    for move in playable_moves(position, context):
        if position.b[move.tr][move.tc]:
            continue
        probe = position.copy(); probe.apply_unchecked(move)
        if legal_moves(probe):
            choices.append(move)
    assert choices, "Fixture must keep both sides able to continue"
    return rng.choice(choices)


@pytest.mark.parametrize("host_red", [True, False])
def test_room_ownership_illegal_moves_stale_requests_and_sixty_plies(host_red):
    room = Room(host_red)
    with pytest.raises(ProtocolError):
        room.apply("host", command(room, "move", move="h2e2"))
    room.connection(True)
    wrong = "guest" if host_red else "host"
    with pytest.raises(ProtocolError):
        room.apply(wrong, command(room, "move", move="h2e2"))
    role = "host" if host_red else "guest"
    with pytest.raises(ProtocolError):
        room.apply(role, command(room, "move", move="a0a9"))
    stale = command(room, "move", move="h2e2")
    room.apply(role, stale)
    with pytest.raises(ProtocolError):
        room.apply(role, stale)
    # Deterministic, non-capturing legal continuation that obeys the new rule.
    rng = random.Random(20261019)
    context = advance((), room.positions[0], Move.from_ucci("h2e2"))
    for _ in range(60):
        move = continuation(room.position,context,rng)
        context = advance(context, room.position, move)
        role = "host" if room.position.red == host_red else "guest"
        room.apply(role, command(room, "move", move=move.ucci()))
    assert len(room.moves) == 61
    verifier = StateVerifier()
    assert verifier.check(room.snapshot())["fen"] == room.position.fen()


def test_negotiation_requires_opponent_and_revisions_never_rewind():
    room = Room()
    room.connection(True)
    room.apply("host", command(room, "move", move="h2e2"))
    room.apply("guest", command(room, "request", action="undo"))
    request = room.pending["id"]
    with pytest.raises(ProtocolError):
        room.apply("guest", command(room, "reply", id=request, accept=True))
    with pytest.raises(ProtocolError):
        room.apply("guest", command(room, "move", move="h7e7"))
    rev = room.revision
    room.apply("host", command(room, "reply", id=request, accept=True))
    assert room.moves == [] and room.revision > rev
    old_game = room.game
    room.apply("host", command(room, "request", action="restart"))
    room.apply("guest", command(room, "reply", id=room.pending["id"], accept=True))
    assert room.game != old_game
    room.apply("guest", command(room, "request", action="draw"))
    room.apply("host", command(room, "reply", id=room.pending["id"], accept=False))
    assert not room.result
    room.apply("host", command(room, "request", action="draw"))
    room.apply("guest", command(room, "reply", id=room.pending["id"], accept=True))
    assert room.result == "双方同意和棋"
    with pytest.raises(ProtocolError):
        room.apply("host", command(room, "move", move="h2e2"))


def test_resign_disconnect_and_terminal_positions():
    room = Room()
    room.connection(True)
    room.apply("host", command(room, "request", action="draw"))
    room.connection(False)
    assert room.pending is None
    with pytest.raises(ProtocolError):
        room.apply("host", command(room, "resign"))
    room.connection(True)
    room.apply("host", command(room, "resign"))
    assert "黑方获胜" in room.result
    room.apply("host", command(room, "request", action="restart"))
    room.apply("guest", command(room, "reply", id=room.pending["id"], accept=True))
    room.position = Position("4k4/4R4/9/9/9/9/9/9/9/4K4 w - - 0 1")
    room.apply("host", command(room, "move", move="e8e9"))
    assert "红方获胜" in room.result


@pytest.mark.parametrize("address", ["8.8.8.8", "127.0.0.1", "0.0.0.0", "169.254.1.2", "100.64.0.1", "::1", "2001:4860::1"])
def test_non_lan_addresses_rejected(address):
    assert not lan_network.private_ipv4(address)
    interface = Interface("192.168.1.20", 24, 9, "LAN", "Private", True)
    assert not interface.contains(address)
    with pytest.raises(ValueError):
        LanClient(interface, address, "abcdefghijklmnop")


def test_subnet_scope_and_fail_closed_policy():
    item = Interface("192.168.1.20", 24, 9, "LAN", "Private", True)
    item.validate()
    assert item.contains("192.168.1.21")
    for ip in ["192.168.2.21", "10.0.0.1", "192.168.1.0", "192.168.1.255"]:
        assert not item.contains(ip)
    for category, enabled in [("Public", True), ("Private", False), ("DomainAuthenticated", True)]:
        with pytest.raises(ValueError):
            Interface(item.ip, 24, 9, "LAN", category, enabled).validate()
    with pytest.raises(ValueError):
        Interface(item.ip, 0, 9, "LAN", "Private", True).validate()


def test_state_snapshot_validation_and_undo():
    room = Room()
    room.connection(True)
    validator = StateVerifier()
    validator.check(room.snapshot())
    room.apply("host", command(room, "move", move="h2e2"))
    validator.check(room.snapshot())
    bad = copy.deepcopy(room.snapshot())
    bad["fen"] = Position().fen()
    with pytest.raises(ProtocolError):
        validator.check(bad)
    room.apply("guest", command(room, "request", action="undo"))
    room.apply("host", command(room, "reply", id=room.pending["id"], accept=True))
    validator.check(room.snapshot())
    assert validator.moves == []


def test_framing_length_and_authentication():
    a, b = socket.socketpair()
    a.settimeout(.1)
    b.settimeout(.1)
    try:
        a.sendall(struct.pack("!I", MAX_FRAME + 1))
        with pytest.raises(ProtocolError):
            receive_frame(b)
        server, client = Channel(a, "abcdefghijklmnop", "n", True), Channel(b, "abcdefghijklmnop", "n")
        server.send({"type": "pong"})
        assert client.receive()["type"] == "pong"
        send_frame(a, {"seq": 1, "payload": {"type": "state"}, "mac": "0" * 64})
        with pytest.raises(ProtocolError):
            client.receive()
    finally:
        close_socket(a)
        close_socket(b)


@dataclass(frozen=True)
class LoopbackInterface:
    """Test-only policy. Production code has no loopback opt-in switch."""
    ip: str = "127.0.0.1"

    def contains(self, ip):
        return ip == self.ip


def wait_event(events, predicate, timeout=6):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = events.get(timeout=.2)
        except queue.Empty:
            continue
        if predicate(value):
            return value
    raise AssertionError("Expected LAN event did not arrive")


@pytest.mark.parametrize("host_red", [True, False])
def test_real_tcp_sixty_plies_reconnect_and_room_identity(monkeypatch, host_red):
    interface = LoopbackInterface()
    monkeypatch.setattr(lan_network, "current_interface", lambda ip: interface)
    host_events, guest_events = queue.Queue(), queue.Queue()
    server = LanServer(interface, host_red, port=0, on_event=host_events.put)
    server.start()
    client = LanClient(interface, interface.ip, server.code, server.port, guest_events.put)
    try:
        with pytest.raises(RuntimeError):
            server.start()
        wrong = LanClient(interface, interface.ip, "abcdefghijklmnop", server.port)
        try:
            with pytest.raises((OSError, ValueError)):
                wrong.connect()
        finally:
            wrong.close()
        legacy = LanClient(interface,interface.ip,server.code,server.port)
        try:
            with monkeypatch.context() as patch:
                patch.setattr(lan_client,"VERSION",1)
                with pytest.raises(ProtocolError,match="双方更新"):
                    legacy.connect()
        finally:
            legacy.close()
        client.connect()
        state = wait_event(guest_events, lambda e: e["type"] == "state" and e["connected"])
        rng, context = random.Random(20261019), ()
        for index in range(60):
            position=Position(state["fen"])
            move=continuation(position,context,rng)
            context=advance(context,position,move)
            value=move.ucci()
            cmd = {"type": "move", "game": state["game"], "revision": state["revision"], "move": value}
            if (index % 2 == 0) == host_red:
                server.submit(cmd)
            else:
                client.send(cmd)
            state = wait_event(guest_events, lambda e: e["type"] == "state" and len(e["moves"]) == index + 1)
            assert state["fen"] == server.room.position.fen()
        third = LanClient(interface, interface.ip, server.code, server.port)
        try:
            with pytest.raises(ProtocolError):
                third.connect()
        finally:
            third.close()
        close_socket(client.sock)
        wait_event(guest_events, lambda e: e["type"] == "disconnected")
        wait_event(host_events, lambda e: e["type"] == "state" and not e["connected"] and len(e["moves"]) == 60)
        takeover = LanClient(interface, interface.ip, server.code, server.port)
        try:
            with pytest.raises(ProtocolError):
                takeover.connect()
        finally:
            takeover.close()
        client.connect()
        state = wait_event(guest_events, lambda e: e["type"] == "state" and e["connected"])
        assert len(state["moves"]) == 60
        client.send({"type": "request", "game": state["game"], "revision": state["revision"], "action": "restart"})
        state = wait_event(guest_events, lambda e: e["type"] == "state" and e["pending"])
        server.submit({"type": "reply", "game": state["game"], "revision": state["revision"], "id": state["pending"]["id"], "accept": True})
        state = wait_event(guest_events, lambda e: e["type"] == "state" and not e["moves"])
        assert state["fen"] == Position().fen()
    finally:
        client.close()
        server.close()
    assert not any(t.is_alive() for t in server.threads + client.threads)
    assert server.listener.fileno() == -1


def test_powershell_missing_rule_and_plain_errors():
    import os
    if os.name != "nt":
        pytest.skip("Windows PowerShell integration")
    # Read-only: absence is expected on first creation, not a setup failure.
    assert lan_network.powershell(
        "Get-NetFirewallRule -PolicyStore ActiveStore "
        "-Name 'XiangqiCoach-LAN-nonexistent-regression' -ErrorAction SilentlyContinue"
    ) == ""
    with pytest.raises(RuntimeError) as error:
        lan_network.powershell("throw 'firewall-test-error'")
    assert str(error.value) == "网络配置失败：firewall-test-error"


def test_firewall_command_is_scoped_without_changing_profiles(monkeypatch):
    item = Interface("192.168.1.20", 24, 9, "LAN", "Private", True)
    monkeypatch.setattr(lan_network, "current_interface", lambda ip: item)
    calls = []
    def invoke(script, **kw):
        calls.append(script)
        return "OK" if len(calls) == 3 else ""
    monkeypatch.setattr(lan_network, "powershell", invoke)
    lan_network.ensure_firewall(item, 39741)
    assert "'192.168.1.0/24'" in calls[0]
    assert "'192.168.1.0/255.255.255.0'" in calls[0]
    assert "@($a.RemoteAddress).Count -eq 1" in calls[0]
    import base64
    import re
    encoded = re.search(r"EncodedCommand ([A-Za-z0-9+/=]+)'", calls[1]).group(1)
    setup = base64.b64decode(encoded).decode("utf-16-le")
    for expected in ["-Profile Private", "-LocalAddress '192.168.1.20'", "-RemoteAddress '192.168.1.0/24'", "-LocalPort 39741", "-EdgeTraversalPolicy Block", "-Program", "-InterfaceAlias 'LAN'"]:
        assert expected in setup
    assert "Set-NetConnectionProfile" not in setup and "Set-NetFirewallProfile" not in setup
