"""MMUKO calibration sequence primitives.

The calibration tuple maps raw byte windows into a one-dimensional state vector:
NOISE, NONOISE, SIGNAL, or NOSIGNAL.
"""

from __future__ import annotations

import enum
import hashlib
import math
import os
import time
from dataclasses import dataclass, field
from typing import Optional


class ByteState(enum.IntEnum):
    """The four calibration states."""

    NOISE = 0
    NONOISE = 1
    SIGNAL = 2
    NOSIGNAL = 3


STATE_LABELS = {state: state.name for state in ByteState}


@dataclass
class CalibrationTuple:
    """C = (NOISE, NONOISE, SIGNAL, NOSIGNAL)."""

    noise_threshold: float = 0.7
    signal_threshold: float = 0.6
    silence_window: int = 8

    def _entropy_score(self, window: bytes) -> float:
        if not window:
            return 0.0
        counts = [0] * 256
        for byte in window:
            counts[byte] += 1
        score = 0.0
        for count in counts:
            if count:
                probability = count / len(window)
                score -= probability * math.log2(probability)
        return score / 8.0

    def _structure_score(self, window: bytes) -> float:
        if not window:
            return 0.0
        unique = len(set(window))
        structure = 1.0 - (unique / max(len(window), 1))
        if len(window) >= 2 and window[0] == 0xAA and window[1] == 0x55:
            structure = min(1.0, structure + 0.3)
        return structure

    def classify(self, window: bytes) -> ByteState:
        if not window:
            return ByteState.NOSIGNAL
        if all(byte == 0x00 for byte in window):
            return ByteState.NOSIGNAL

        entropy = self._entropy_score(window)
        structure = self._structure_score(window)

        if entropy >= self.noise_threshold:
            return ByteState.NOISE
        if structure >= self.signal_threshold:
            return ByteState.SIGNAL
        return ByteState.NONOISE

    def classify_stream(self, stream: bytes, window_size: int = 16) -> list[ByteState]:
        return [self.classify(stream[index : index + window_size]) for index in range(0, len(stream), window_size)]


@dataclass
class CalibrationEvent:
    """A single event in the event-agnostic calibration sequence."""

    kind: str
    payload: bytes = b""
    timestamp: float = field(default_factory=time.time)
    node_id: str = ""


@dataclass
class Transmitter:
    """Emit byte streams tagged with the MMUKO calibration preamble."""

    node_id: str = "TX-001"

    PREAMBLE = bytes([0xAA, 0x55])

    def emit(self, payload: bytes) -> CalibrationEvent:
        return CalibrationEvent(kind="data", payload=self.PREAMBLE + payload, node_id=self.node_id)

    def connect(self) -> CalibrationEvent:
        return CalibrationEvent(kind="connect", node_id=self.node_id)

    def disconnect(self) -> CalibrationEvent:
        return CalibrationEvent(kind="disconnect", node_id=self.node_id)


@dataclass
class Receiver:
    """Classify incoming calibration events into a rolling vector."""

    calibrator: CalibrationTuple = field(default_factory=CalibrationTuple)
    node_id: str = "RX-001"
    _vector: list[ByteState] = field(default_factory=list, init=False)
    _connected: bool = field(default=False, init=False)

    def receive(self, event: CalibrationEvent) -> list[ByteState]:
        if event.kind == "connect":
            self._connected = True
            return []
        if event.kind == "disconnect":
            self._connected = False
            return []
        if not self._connected:
            return []

        states = self.calibrator.classify_stream(event.payload)
        self._vector.extend(states)
        return states

    @property
    def calibration_vector(self) -> list[ByteState]:
        return list(self._vector)

    def dominant_state(self) -> Optional[ByteState]:
        if not self._vector:
            return None
        return max(set(self._vector), key=self._vector.count)


@dataclass
class Verifier:
    """Validate a receiver calibration vector."""

    node_id: str = "VRF-001"

    def verify(self, receiver: Receiver) -> tuple[bool, str]:
        vector = receiver.calibration_vector
        if not vector:
            return False, "empty-vector"

        dominant = receiver.dominant_state()
        is_valid = dominant == ByteState.SIGNAL
        raw = bytes([int(state) for state in vector])
        fingerprint = hashlib.sha256(raw).hexdigest()[:16]
        return is_valid, fingerprint


class CalibrationSession:
    """Run a complete MMUKO calibration sequence."""

    def __init__(
        self,
        transmitter: Optional[Transmitter] = None,
        receiver: Optional[Receiver] = None,
        verifier: Optional[Verifier] = None,
    ) -> None:
        self.tx = transmitter or Transmitter()
        self.rx = receiver or Receiver()
        self.vrf = verifier or Verifier()

    def run(self, payloads: list[bytes]) -> dict[str, object]:
        self.rx.receive(self.tx.connect())
        all_states: list[list[ByteState]] = []
        for payload in payloads:
            states = self.rx.receive(self.tx.emit(payload))
            all_states.append(states)
        valid, fingerprint = self.vrf.verify(self.rx)
        self.rx.receive(self.tx.disconnect())
        dominant = self.rx.dominant_state()
        return {
            "connected": valid,
            "fingerprint": fingerprint,
            "dominant": dominant.name if dominant is not None else None,
            "vector_len": len(self.rx.calibration_vector),
            "states": [[state.name for state in group] for group in all_states],
        }


def summarize_stream(stream: bytes, window_size: int = 16, calibrator: CalibrationTuple | None = None) -> dict[str, object]:
    active_calibrator = calibrator or CalibrationTuple()
    states = active_calibrator.classify_stream(stream, window_size=window_size)
    counts = {state.name: states.count(state) for state in ByteState}
    dominant = max(states, key=states.count) if states else ByteState.NOSIGNAL
    raw = bytes([int(state) for state in states])
    return {
        "states": [state.name for state in states],
        "counts": counts,
        "dominant": dominant.name,
        "fingerprint": hashlib.sha256(raw).hexdigest()[:16] if raw else "empty-vector",
    }


def demo_payloads() -> list[bytes]:
    return [
        bytes([0xAA, 0x55]) + b"OBINexus::NSIGII::CONNECT",
        os.urandom(32),
        bytes(16),
        b"clean-channel",
    ]
