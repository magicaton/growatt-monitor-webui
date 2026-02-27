import logging
import uuid
import itertools
from collections import deque


class MemoryLogHandler(logging.Handler):
    def __init__(self, max_entries=10000):
        super().__init__()
        self.max_entries = max_entries
        self.log_buffer = deque(maxlen=max_entries)
        self.listeners = {}

    def emit(self, record):
        try:
            entry = {
                "ts": record.created,
                "level": record.levelname,
                "msg": record.getMessage(),
                "name": record.name,
            }

            # Thread-safe: emit() is called with self.lock held by handle()
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

    def get_entries(self, start=0, count=None):
        self.acquire()
        try:
            if count is None:
                return list(itertools.islice(self.log_buffer, start, None))
            return list(itertools.islice(self.log_buffer, start, start + count))
        finally:
            self.release()

    def __len__(self):
        return len(self.log_buffer)



def configure_logging(console_level, file_level, log_path):
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(console_level)

    if log_path:
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
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
