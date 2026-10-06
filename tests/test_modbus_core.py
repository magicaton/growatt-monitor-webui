import struct

import pytest
import serial

from core.modbus_core import build_optimized_chunks, calculate_crc, read_chunk_sync


def make_response(slave_id, values, func=4):
    body = struct.pack(">BBB", slave_id, func, len(values) * 2)
    body += b"".join(struct.pack(">H", v) for v in values)
    return body + calculate_crc(body)


class FakeSerial:
    # Minimal stand-in for serial.Serial: replies are queued per request,
    # `pending` holds bytes already waiting in the input buffer.
    def __init__(self, replies=(), pending=b"", write_error=None):
        self.is_open = True
        self.replies = list(replies)
        self.buffer = bytearray(pending)
        self.write_error = write_error
        self.calls = []

    def reset_input_buffer(self):
        self.calls.append("reset")
        self.buffer.clear()

    def write(self, data):
        self.calls.append("write")
        if self.write_error:
            raise self.write_error
        if self.replies:
            self.buffer += self.replies.pop(0)

    def read(self, size):
        data = bytes(self.buffer[:size])
        del self.buffer[:size]
        return data


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("core.modbus_core.time.sleep", lambda s: None)


def test_crc_matches_modbus_reference():
    assert calculate_crc(bytes.fromhex("010400000001")) == bytes.fromhex("31ca")


def test_build_optimized_chunks():
    assert build_optimized_chunks(set(), 40, 20) == []
    assert build_optimized_chunks({0, 5, 10}, 40, 20) == [(0, 11)]
    # Gap larger than max_gap splits the chunk
    assert build_optimized_chunks({0, 10, 30, 83, 84}, 40, 20) == [(0, 31), (83, 2)]
    assert build_optimized_chunks({0, 37}, 40, 20) == [(0, 1), (37, 1)]
    # Chunk size is capped at max_count
    assert build_optimized_chunks({0, 20, 39, 40}, 40, 20) == [(0, 40), (40, 1)]


def test_read_valid_response():
    ser = FakeSerial([make_response(1, [100, 200, 65535])])
    assert read_chunk_sync(ser, 1, 0, 3) == [100, 200, 65535]


def test_input_buffer_is_cleared_before_request():
    # A late reply to an earlier request must not shift this read
    stale = make_response(1, [9, 9])
    ser = FakeSerial([make_response(1, [1, 2])], pending=stale)
    assert read_chunk_sync(ser, 1, 0, 2) == [1, 2]
    assert ser.calls[:2] == ["reset", "write"]


def test_short_response_returns_none():
    ser = FakeSerial([make_response(1, [1, 2])[:5]])
    assert read_chunk_sync(ser, 1, 0, 2) is None


def test_crc_mismatch_returns_none():
    reply = bytearray(make_response(1, [1, 2]))
    reply[-1] ^= 0xFF
    ser = FakeSerial([bytes(reply)])
    assert read_chunk_sync(ser, 1, 0, 2) is None


def test_exception_response_is_reported(caplog):
    body = struct.pack(">BBB", 1, 0x84, 2)
    ser = FakeSerial([body + calculate_crc(body)])
    assert read_chunk_sync(ser, 1, 0, 2) is None
    assert "Modbus exception code 2" in caplog.text


def test_unexpected_header_returns_none():
    # Valid CRC, but from another slave id
    ser = FakeSerial([make_response(2, [1, 2])])
    assert read_chunk_sync(ser, 1, 0, 2) is None


def test_port_failure_is_raised():
    ser = FakeSerial(write_error=serial.SerialException("WriteFile failed"))
    with pytest.raises(serial.SerialException):
        read_chunk_sync(ser, 1, 0, 2)


def test_reset_failure_is_raised_as_serial_exception():
    class GoneSerial(FakeSerial):
        def reset_input_buffer(self):
            raise OSError("device gone")  # e.g. termios.error on POSIX

    with pytest.raises(serial.SerialException):
        read_chunk_sync(GoneSerial(), 1, 0, 2)


def test_closed_port_returns_none():
    ser = FakeSerial()
    ser.is_open = False
    assert read_chunk_sync(ser, 1, 0, 2) is None
    assert read_chunk_sync(None, 1, 0, 2) is None
