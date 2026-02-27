import time
import struct
import logging
import serial
import serial.tools.list_ports

# --- AUTO-DETECT COM PORT ---
def auto_detect_com_port(baudrate: int, slave_id: int) -> str | None:
    ports = serial.tools.list_ports.comports()
    for port_info in ports:
        com = port_info.device
        logging.debug("Testing port %s for auto-detect", com)
        try:
            ser = serial.Serial(com, baudrate, timeout=1.0)
            try:
                ser.reset_input_buffer()
                vals = read_chunk_sync(ser, slave_id, 0, 1)
            finally:
                ser.close()
            if vals is not None:
                logging.info("Auto-detected Modbus device on %s", com)
                return com
        except Exception as e:
            logging.debug("Port %s test failed: %s", com, e)
    return None

# --- CHUNK OPTIMIZATION ---
# Greedy merge: walk sorted register IDs and extend the current chunk
# as long as the gap to next ID ≤ max_gap and total size ≤ max_count.
def build_optimized_chunks(required_ids: set[int], max_count: int, max_gap: int) -> list[tuple[int, int]]:
    if not required_ids:
        return []

    sorted_regs = sorted(required_ids)

    chunks = []
    current_start = sorted_regs[0]
    current_end = sorted_regs[0]

    for reg in sorted_regs[1:]:
        next_count = (reg - current_start) + 1
        gap = reg - (current_end + 1)

        if gap <= max_gap and next_count <= max_count:
            current_end = reg
        else:
            count = (current_end - current_start) + 1
            chunks.append((current_start, count))
            current_start = reg
            current_end = reg

    count = (current_end - current_start) + 1
    chunks.append((current_start, count))

    return chunks


# --- MODBUS RTU ---
def calculate_crc(data):
    crc = 0xFFFF
    for char in data:
        crc ^= char
        for _ in range(8):
            if crc & 0x0001:
                crc >>= 1
                crc ^= 0xA001
            else:
                crc >>= 1
    return struct.pack("<H", crc)

def read_chunk_sync(ser, slave_id, start_addr, count):
    try:
        if ser is None or not ser.is_open:
            return None
        # Modbus RTU request: [slave_id, func=4 (Read Input Registers), start_addr(2B), count(2B), CRC(2B)]
        pdu = struct.pack(">BBHH", slave_id, 4, start_addr, count)
        crc = calculate_crc(pdu)
        ser.write(pdu + crc)
        time.sleep(0.15)  # Wait for device to process and respond
        # Response: [slave_id(1B), func(1B), byte_count(1B), data(count*2B), CRC(2B)]
        expected_len = 5 + (count * 2)
        response = ser.read(expected_len)
        time.sleep(0.05)  # Inter-frame gap before next request
        if len(response) < expected_len:
            logging.warning(
                "Short Modbus response for reg %d (x%d): expected %d bytes, got %d",
                start_addr,
                count,
                expected_len,
                len(response),
            )
            return None
        received_crc = response[-2:]
        expected_crc = calculate_crc(response[:-2])
        if received_crc != expected_crc:
            logging.warning(
                "CRC mismatch for reg %d (x%d): expected %s, got %s",
                start_addr, count, expected_crc.hex(), received_crc.hex(),
            )
            return None
        data_bytes = response[3:-2]  # Strip header (3B) and CRC (2B)
        values = []
        for i in range(0, len(data_bytes), 2):
            val = int.from_bytes(data_bytes[i : i + 2], byteorder="big")
            values.append(val)
        return values
    except Exception:
        logging.exception(
            "Modbus read error for reg %d (x%d)", start_addr, count
        )
        return None
