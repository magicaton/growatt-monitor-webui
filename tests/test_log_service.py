import logging
import os

from core.log_service import MemoryLogHandler, SafeRotatingFileHandler


def make_logger(handler, name):
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    return logger


def test_entries_keep_their_seq_after_buffer_wraps():
    handler = MemoryLogHandler(max_entries=5)
    logger = make_logger(handler, "test_seq")
    for i in range(12):
        logger.info("msg %d", i)

    assert handler.next_seq == 12
    assert handler.first_seq == 7
    assert len(handler) == 5

    entries = handler.get_entries(9, 2)
    assert [e["seq"] for e in entries] == [9, 10]
    assert [e["msg"] for e in entries] == ["msg 9", "msg 10"]

    # Evicted range is clamped to what is still buffered
    assert [e["seq"] for e in handler.get_entries(5, 4)] == [7, 8]
    assert handler.get_entries(0, 3) == []
    assert [e["seq"] for e in handler.get_entries(10)] == [10, 11]


def test_listener_receives_seq():
    handler = MemoryLogHandler(max_entries=3)
    logger = make_logger(handler, "test_listener")
    received = []
    handler.add_listener(received.append)
    for i in range(5):
        logger.info("msg %d", i)
    assert [e["seq"] for e in received] == [0, 1, 2, 3, 4]


def test_rotation(tmp_path):
    path = str(tmp_path / "app.log")
    handler = SafeRotatingFileHandler(path, maxBytes=100, backupCount=2, encoding="utf-8")
    logger = make_logger(handler, "test_rotation")
    for i in range(10):
        logger.info("record %02d %s", i, "x" * 30)
    handler.close()

    assert os.path.exists(path + ".1")
    assert os.path.exists(path + ".2")
    assert not os.path.exists(path + ".3")


def test_rotation_skipped_while_file_is_held(tmp_path, monkeypatch):
    # Windows: the log can't be renamed while another process has it open
    path = str(tmp_path / "app.log")
    with open(path + ".1", "w", encoding="utf-8") as f:
        f.write("BACKUP-1\n")

    real_replace = os.replace
    held = True

    def fake_replace(src, dst):
        if held and os.path.abspath(src) == path:
            raise PermissionError(32, "being used by another process", src)
        return real_replace(src, dst)

    monkeypatch.setattr("core.log_service.os.replace", fake_replace)
    handler = SafeRotatingFileHandler(path, maxBytes=100, backupCount=3, encoding="utf-8")
    logger = make_logger(handler, "test_held")
    errors = []
    monkeypatch.setattr(handler, "handleError", lambda record: errors.append(record))

    for i in range(10):
        logger.info("held %02d %s", i, "x" * 30)
    held = False
    logger.info("released %s", "x" * 30)
    handler.close()

    assert errors == []
    contents = ""
    for name in os.listdir(tmp_path):
        with open(tmp_path / name, encoding="utf-8") as f:
            contents += f.read()
    for i in range(10):
        assert f"held {i:02d}" in contents
    assert "released" in contents
    assert "BACKUP-1" in contents
