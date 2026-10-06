import os
import logging
import uuid
import itertools
from collections import deque
from logging.handlers import RotatingFileHandler


class MemoryLogHandler(logging.Handler):
    def __init__(self, max_entries=10000):
        super().__init__()
        self.max_entries = max_entries
        self.log_buffer = deque(maxlen=max_entries)
        self.listeners = {}
        # Monotonic sequence number of the next entry. Positions in log_buffer
        # shift once it is full, so callers address entries by "seq" instead.
        self.next_seq = 0

    def emit(self, record):
        try:
            entry = {
                "seq": self.next_seq,
                "ts": record.created,
                "level": record.levelname,
                "msg": record.getMessage(),
                "name": record.name,
            }

            # Thread-safe: emit() is called with self.lock held by handle()
            self.next_seq += 1
            self.log_buffer.append(entry)


            for callback in list(self.listeners.values()):
                try:
                    callback(entry)
                except Exception:
                    pass
        except Exception:
            self.handleError(record)

    def add_listener(self, callback):
        listener_id = str(uuid.uuid4())
        self.acquire()  # Uses logging.Handler's built-in threading lock
        try:
            self.listeners[listener_id] = callback
        finally:
            self.release()
        return listener_id

    def remove_listener(self, listener_id):
        self.acquire()
        try:
            if listener_id in self.listeners:
                del self.listeners[listener_id]
        finally:
            self.release()

    @property
    def first_seq(self):
        # seq of the oldest entry still in the buffer
        self.acquire()
        try:
            return self.next_seq - len(self.log_buffer)
        finally:
            self.release()

    def get_entries(self, start_seq=0, count=None):
        # Entries with start_seq <= seq < start_seq + count that are still buffered
        self.acquire()
        try:
            offset = start_seq - (self.next_seq - len(self.log_buffer))
            stop = None if count is None else max(0, offset + count)
            return list(itertools.islice(self.log_buffer, max(0, offset), stop))
        finally:
            self.release()

    def __len__(self):
        return len(self.log_buffer)



class SafeRotatingFileHandler(RotatingFileHandler):
    # On Windows the log can't be renamed while another process holds it open
    # (Get-Content -Wait, antivirus, the previous instance during a restart).
    # The stock handler then drops records and shifts backups on every attempt;
    # here rotation is skipped and retried on a later record instead.
    def doRollover(self):
        if self.stream:
            self.stream.close()
            self.stream = None
        rotating = self.baseFilename + ".rotating"
        try:
            os.replace(self.baseFilename, rotating)
        except OSError:
            self.stream = self._open()  # keep appending to the current file
            return
        try:
            for i in range(self.backupCount - 1, 0, -1):
                src = f"{self.baseFilename}.{i}"
                if os.path.exists(src):
                    os.replace(src, f"{self.baseFilename}.{i + 1}")
            os.replace(rotating, self.baseFilename + ".1")
        except OSError:
            pass  # A backup is held open; keep logging and leave the shift for now
        self.stream = self._open()


def configure_logging(console_level, file_level, log_path):
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(console_level)

    if log_path:
        file_handler = SafeRotatingFileHandler(
            log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(file_level)

    memory_handler = MemoryLogHandler()
    memory_handler.setFormatter(formatter)
    memory_handler.setLevel(console_level)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    log_level = min(console_level, file_level) if log_path else console_level
    root_logger.setLevel(log_level)
    root_logger.addHandler(console_handler)
    if log_path:
        root_logger.addHandler(file_handler)
    root_logger.addHandler(memory_handler)

    return memory_handler
