import os
import yaml
import logging
from dataclasses import dataclass, field

@dataclass
class Config:
    # Logging
    console_log_level: int = logging.INFO
    file_log_level: int = logging.WARNING

    # Server
    server_host: str = "0.0.0.0"
    server_port: int = 8080
    storage_secret: str = "secret_key_1234567890"

    # Modbus / Serial
    com_port: str = "auto"
    baudrate: int = 9600
    slave_id: int = 1
    update_interval: float = 3.0
    max_chunk_size: int = 40
    opt_max_gap: int = 20

    # UI defaults (can be overridden per-browser via query params)
    show_fs_btn: bool = False
    show_dev_btns: bool = True

    # Inspector mode chunks (start_addr, count)
    inspector_chunks: list[tuple[int, int]] = field(
        default_factory=lambda: [(0, 40), (40, 40), (80, 40)]
    )

    def load_from_file(self, path: str) -> None:
        if not os.path.exists(path):
            raise FileNotFoundError(f"Config file not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if "logging" in data:
            log_cfg = data["logging"]
            if "console_level" in log_cfg:
                self.console_log_level = self._parse_log_level(log_cfg["console_level"])
            if "file_level" in log_cfg:
                self.file_log_level = self._parse_log_level(log_cfg["file_level"])

        if "server" in data:
            server_cfg = data["server"]
            if "host" in server_cfg:
                self.server_host = server_cfg["host"]
            if "port" in server_cfg:
                self.server_port = int(server_cfg["port"])
            if "storage_secret" in server_cfg:
                self.storage_secret = str(server_cfg["storage_secret"])

        if "modbus" in data:
            mb_cfg = data["modbus"]
            if "com_port" in mb_cfg:
                self.com_port = mb_cfg["com_port"]
            if "baudrate" in mb_cfg:
                self.baudrate = int(mb_cfg["baudrate"])
            if "slave_id" in mb_cfg:
                self.slave_id = int(mb_cfg["slave_id"])
            if "update_interval" in mb_cfg:
                self.update_interval = float(mb_cfg["update_interval"])
            if "max_chunk_size" in mb_cfg:
                self.max_chunk_size = int(mb_cfg["max_chunk_size"])
            if "opt_max_gap" in mb_cfg:
                self.opt_max_gap = int(mb_cfg["opt_max_gap"])

        if "ui" in data:
            ui_cfg = data["ui"]
            if "show_fs_btn" in ui_cfg:
                self.show_fs_btn = bool(ui_cfg["show_fs_btn"])
            if "show_dev_btns" in ui_cfg:
                self.show_dev_btns = bool(ui_cfg["show_dev_btns"])

        if "inspector" in data:
            insp_cfg = data["inspector"]
            if "inspector_chunks" in insp_cfg:
                self.inspector_chunks = [
                    (int(c[0]), int(c[1])) for c in insp_cfg["inspector_chunks"]
                ]

    @staticmethod
    def _parse_log_level(value: str | int) -> int:
        if isinstance(value, int):
            return value
        level_map = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL,
        }
        result = level_map.get(value.upper())
        if result is None:
            logging.warning("Unknown log level '%s', defaulting to INFO", value)
            return logging.INFO
        return result
