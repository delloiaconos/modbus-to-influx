#!/usr/bin/env python3

import time
import datetime
import json
import math
import struct
import logging
from pathlib import Path
from os import environ as env
from influxdb import InfluxDBClient
from pyModbusTCP.client import ModbusClient


LOGGER = logging.getLogger(__name__)

MODBUS_HOST       = env.get( 'MODBUS_HOST'       , '192.168.0.10' )
MODBUS_PORT       = int( env.get( 'MODBUS_PORT'  , '502' ) )

INFLUXDB_HOST     = env.get( 'INFLUXDB_HOST'     , '172.16.1.10' )
INFLUXDB_PORT     = int( env.get( 'INFLUXDB_PORT', '8086' ) )
INFLUXDB_USER     = env.get( 'INFLUXDB_USER'     , 'influx_user' )
INFLUXDB_PASSWORD = env.get( 'INFLUXDB_PASSWORD' , 'influx_pass' )
INFLUXDB_DATABASE = env.get( 'INFLUXDB_DATABASE' , 'influxdb' )

SLEEP_READOUT    = int( env.get( 'SLEEP_READOUT' , '5' ))
SLEEP_RETRY      = int( env.get( 'SLEEP_RETRY'   , '120' ))
CLIENT_TIMEOUT   = int( env.get( 'CLIENT_TIMEOUT' , '10' ))

REGISTERS_FILE = env.get( 'REGISTERS_FILE', "/config/registers.json" )


# struct format and number of 16-bit registers read per value.
BYTE_TYPES = ("char", "byte", "uint8", "int8")
VECTOR_TYPES = ("string", "bytes")
REGISTER_TYPES = {
    "float32": ("f", 2), "float64": ("d", 4),
    "int16": ("h", 1), "int32": ("i", 2), "int64": ("q", 4),
    "uint16": ("H", 1), "uint32": ("I", 2), "uint64": ("Q", 4),
    "bit": ("H", 1),
    "char": ("c", 1), "byte": ("B", 1),
    "uint8": ("B", 1), "int8": ("b", 1),
    # Vector register widths are computed from their byte lengths.
    "string": ("s", None), "bytes": ("s", None),
}


def register_width(conversion):
    """Return the register count, rounding vector byte lengths up to whole words."""
    if conversion["type"] in VECTOR_TYPES:
        length = conversion["length"]
        if type(length) is not int or length <= 0:
            raise ValueError("Vector length must be a positive integer number of bytes")
        return (length + 1) // 2
    return REGISTER_TYPES[conversion["type"]][1]


def load_registers(path):
    """Return validated block definitions from a UTF-8 JSON file.

    File/JSON errors and invalid definitions propagate to the retry loop before
    any client is created. Duplicate fields in the same series are rejected.
    """

    with open(path, encoding="utf-8") as source:
        structure = json.load(source)
    if not isinstance(structure, list) or not structure:
        raise ValueError("Register structure must be a nonempty list")
    series_names = {}
    for position, block in enumerate(structure):
        label = f"Register block {position}"
        if not isinstance(block, dict) or set(block) != {"address", "length", "measurement", "tags", "convert"}:
            raise ValueError(f"{label} requires address, length, measurement, tags and convert")
        measurement = block["measurement"]
        if not isinstance(measurement, str) or not measurement.strip():
            raise ValueError(f"{label}: measurement must be a nonempty string")
        if not isinstance(block["tags"], list):
            raise ValueError(f"{label}: tags must be a list")
        tags = {}
        for tag in block["tags"]:
            if not isinstance(tag, dict) or set(tag) != {"name", "value"}:
                raise ValueError(f"{label}: each tag requires name and value")
            name, value = tag["name"], tag["value"]
            if not isinstance(name, str) or not name.strip() or name in tags:
                raise ValueError(f"{label}: tag names must be nonempty and unique")
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label}: tag values must be nonempty strings")
            tags[name] = value
        # Blocks in the same series share a timestamp; prevent field overwrites.
        series = (measurement, tuple(sorted(tags.items())))
        names = series_names.setdefault(series, set())
        address, length = block["address"], block["length"]
        if type(address) is not int or not 0 <= address <= 65535:
            raise ValueError(f"{label}: address must be an integer from 0 to 65535")
        if type(length) is not int or not 1 <= length <= 125 or address + length > 65536:
            raise ValueError(f"{label}: invalid holding-register length or address range")
        if not isinstance(block["convert"], list) or not block["convert"]:
            raise ValueError(f"{label}: convert must be a nonempty list")
        for conversion in block["convert"]:
            required = {"name", "index", "type", "order", "scale", "offset"}
            if not isinstance(conversion, dict):
                raise ValueError(f"{label}: conversion must be an object")
            if conversion.get("type") == "bit":
                required.add("bit")
            if conversion.get("type") in VECTOR_TYPES:
                required.add("length")
            optional = {"byte"} if conversion.get("type") in BYTE_TYPES else set()
            if not required <= set(conversion) or set(conversion) - required - optional:
                raise ValueError(
                    f"{label}: conversion requires {', '.join(sorted(required))}; "
                    f"optional properties: {', '.join(sorted(optional)) or 'none'}"
                )
            name, index = conversion["name"], conversion["index"]
            if not isinstance(name, str) or not name.strip() or name in names:
                raise ValueError(f"{label}: field names must be nonempty and unique within a measurement/tag set")
            data_type = conversion["type"]
            if not isinstance(data_type, str) or data_type not in REGISTER_TYPES:
                raise ValueError(f"{label}: unsupported type for {name}")
            width = register_width(conversion)
            if type(index) is not int or not 0 <= index <= length - width:
                raise ValueError(f"{label}: all registers for {name} must be within the block")
            if conversion["order"] not in ("msb", "lsb"):
                raise ValueError(f"{label}: order for {name} must be msb or lsb")
            for setting in ("scale", "offset"):
                value = conversion[setting]
                if type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)):
                    raise ValueError(f"{label}: {setting} for {name} must be a finite number")
            if data_type == "bit" and (type(conversion["bit"]) is not int or not 0 <= conversion["bit"] <= 15):
                raise ValueError(f"{label}: bit for {name} must be an integer from 0 to 15")
            if data_type in BYTE_TYPES and conversion.get("byte", "low") not in ("high", "low"):
                raise ValueError(f"{label}: byte for {name} must be high or low")
            if data_type in ("char", "string", "byte", "bytes") and (conversion["scale"] != 1 or conversion["offset"] != 0):
                raise ValueError(f"{label}: {data_type} field {name} requires scale 1 and offset 0")
            names.add(name)
    return structure


def decode_registers(regs, conversion):
    """Decode a validated field definition and apply value * scale + offset.

    Preserve integer/boolean types for identity transforms. Incomplete values
    and non-finite floating-point results raise ValueError. Byte types select
    one byte from a register; char and fixed-length strings return Latin-1 text
    without stripping whitespace or NUL bytes. Byte and bytes values return
    lowercase hexadecimal strings with two digits per byte and no prefix.
    """

    fmt = REGISTER_TYPES[conversion["type"]][0]
    width = register_width(conversion)
    index = conversion["index"]
    words = regs[index:index + width]
    if len(words) != width:
        raise ValueError(f"Incomplete value for {conversion['name']}")
    if conversion["order"] == "lsb":
        words = words[::-1]
    # Modbus words are already integers; keep the high byte first within each word.
    raw = b"".join(word.to_bytes(2, "big") for word in words)
    if conversion["type"] in VECTOR_TYPES:
        # Order complete words before taking the requested bytes. For odd
        # lengths, discard only the final unused byte, not payload padding.
        raw = raw[:conversion["length"]]
        return raw.hex() if conversion["type"] == "bytes" else raw.decode("latin-1")
    if conversion["type"] in BYTE_TYPES:
        byte_index = 0 if conversion.get("byte", "low") == "high" else 1
        raw = raw[byte_index:byte_index + 1]
    if conversion["type"] == "byte":
        return raw.hex()
    value = struct.unpack(">" + fmt, raw)[0]
    if conversion["type"] == "char":
        # Latin-1 gives every byte an exact, reversible character mapping.
        return value.decode("latin-1")
    if conversion["type"] == "bit":
        value = bool(value & (1 << conversion["bit"]))
    # Preserve exact 64-bit integers and booleans for identity transformations.
    if conversion["scale"] != 1 or conversion["offset"] != 0:
        value = value * conversion["scale"] + conversion["offset"]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"Non-finite value for {conversion['name']}")
    return value


def read_registers(mbus, structure):
    """Build one point per block from an already connected Modbus client.

    Return None for failed/short reads so no partial batch is written. Decode
    and transport exceptions propagate to the caller for cleanup and retry.
    """

    points = []
    for block in structure:
        regs = mbus.read_holding_registers(block["address"], block["length"])
        if regs is None or len(regs) != block["length"]:
            return None
        fields = {}
        for conversion in block["convert"]:
            fields[conversion["name"]] = decode_registers(regs, conversion)
        points.append({
            "measurement": block["measurement"],
            "tags": {tag["name"]: tag["value"] for tag in block["tags"]},
            "fields": fields,
        })
    return points


def send_data_to_influxdb(db, points):
    """Write a complete batch with one UTC timestamp; raise on rejected writes."""
    
    if not points:
        raise ValueError("Cannot write an empty point batch")
    timestamp = datetime.datetime.now(datetime.timezone.utc)
    if not db.write_points([dict(point, time=timestamp) for point in points]):
        raise RuntimeError("InfluxDB did not acknowledge the point batch")


def init_influxdb(db):
    """Check server reachability, create the database if absent, and select it.

    Database queries also check authenticated access; a successful ping alone
    does not establish that this account can read or write the database.
    """
    
    if not db.ping():
        raise ConnectionError("InfluxDB ping returned no server version")
    databases = db.get_list_database()
    if not any(database['name'] == INFLUXDB_DATABASE for database in databases):
        db.create_database(INFLUXDB_DATABASE)
    db.switch_database(INFLUXDB_DATABASE)


def close_clients(mbus, db):
    """Close both clients, even if one close fails, without masking the cause."""
    for name, client in (("Modbus", mbus), ("InfluxDB", db)):
        if client is not None:
            try:
                client.close()
            except Exception:
                LOGGER.exception("Failed to close %s client", name)


def main():
    """Run one connection session; always close clients before returning/raising.

    Failed opens, reads, and writes end the session so run() can reconnect after
    SLEEP_RETRY. Successful polls wait SLEEP_READOUT before the next cycle.
    """
    registers = load_registers(REGISTERS_FILE)
    if SLEEP_READOUT < 0 or SLEEP_RETRY < 0:
        raise ValueError("Polling and retry delays must be nonnegative")
    db = None
    mbus = None
    try:
        db = InfluxDBClient(
            INFLUXDB_HOST, INFLUXDB_PORT, INFLUXDB_USER, INFLUXDB_PASSWORD,
            timeout=CLIENT_TIMEOUT, retries=1,
        )
        init_influxdb(db)

        mbus = ModbusClient(
            host=MODBUS_HOST, port=MODBUS_PORT,
            auto_open=False, timeout=CLIENT_TIMEOUT,
        )
        
        while True:
            # is_open describes the socket; only an actual read confirms health.
            if not mbus.is_open and not mbus.open():
                raise ConnectionError(
                    f"Unable to connect to Modbus {MODBUS_HOST}:{MODBUS_PORT}: "
                    f"{mbus.last_error_as_txt}"
                )
            data = read_registers(mbus, registers)
            if not data:
                raise ConnectionError(
                    f"Incomplete Modbus read: {mbus.last_error_as_txt}; "
                    f"{mbus.last_except_as_txt}"
                )
            # The write itself checks InfluxDB health on every polling cycle.
            send_data_to_influxdb(db, data)
            LOGGER.debug("Wrote %d register blocks", len(data))
            time.sleep(SLEEP_READOUT)
    finally:
        close_clients(mbus, db)


def run():
    """Retry failed sessions with a delay; allow Ctrl+C during polls or waits."""
    try:
        while True:
            try:
                main()
            except Exception:
                LOGGER.exception("Gateway session failed; retrying in %s seconds", SLEEP_RETRY)
            time.sleep(max(0, SLEEP_RETRY))
    except KeyboardInterrupt:
        LOGGER.info("Gateway stopped")


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    LOGGER.info('MODBUS TCP to INFLUX DB')
    run()
