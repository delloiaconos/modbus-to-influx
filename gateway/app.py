#!/usr/bin/env python3

import time
import datetime
import json
import math
import struct
from pathlib import Path
from os import environ as env
from influxdb import InfluxDBClient
from pyModbusTCP.client import ModbusClient


MODBUS_HOST       = env.get( 'MODBUS_HOST'       , '192.168.0.10' )
MODBUS_PORT       = int( env.get( 'MODBUS_PORT'  , '502' ) )

INFLUXDB_HOST     = env.get( 'INFLUXDB_HOST'     , '172.16.1.10' )
INFLUXDB_PORT     = int( env.get( 'INFLUXDB_PORT', '8086' ) )
INFLUXDB_USER     = env.get( 'INFLUXDB_USER'     , 'influx_user' )
INFLUXDB_PASSWORD = env.get( 'INFLUXDB_PASSWORD' , 'influx_pass' )
INFLUXDB_DATABASE = env.get( 'INFLUXDB_DATABASE' , 'influxdb' )

SLEEP_READOUT    = int( env.get( 'SLEEP_READOUT' , '5' ))
SLEEP_RETRY      = int( env.get( 'SLEEP_RETRY'   , '120' ))


REGISTERS_FILE = env.get(
    'REGISTERS_FILE', str(Path(__file__).resolve().parent.parent / 'registers.json')
)


# struct format and number of 16-bit registers per value.
REGISTER_TYPES = {
    "float32": ("f", 2), "float64": ("d", 4),
    "int16": ("h", 1), "int32": ("i", 2), "int64": ("q", 4),
    "uint16": ("H", 1), "uint32": ("I", 2), "uint64": ("Q", 4),
    "bit": ("H", 1),
}


def _load_registers(path):
    """Load and validate the JSON holding-register blocks before polling."""
    with open(path, encoding="utf-8") as source:
        structure = json.load(source)
    if not isinstance(structure, list) or not structure:
        raise ValueError("Register structure must be a nonempty list")
    names = set()
    for position, block in enumerate(structure):
        label = f"Register block {position}"
        if not isinstance(block, dict) or set(block) != {"address", "length", "convert"}:
            raise ValueError(f"{label} requires address, length and convert")
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
            if set(conversion) != required:
                raise ValueError(f"{label}: conversion requires {', '.join(sorted(required))}")
            name, index = conversion["name"], conversion["index"]
            if not isinstance(name, str) or not name.strip() or name in names:
                raise ValueError(f"{label}: field names must be nonempty and unique")
            data_type = conversion["type"]
            if not isinstance(data_type, str) or data_type not in REGISTER_TYPES:
                raise ValueError(f"{label}: unsupported type for {name}")
            width = REGISTER_TYPES[data_type][1]
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
            names.add(name)
    return structure


def _decode_registers(regs, conversion):
    """Decode words (MSB or LSB first), then apply value * scale + offset."""
    fmt, width = REGISTER_TYPES[conversion["type"]]
    index = conversion["index"]
    words = regs[index:index + width]
    if len(words) != width:
        raise ValueError(f"Incomplete value for {conversion['name']}")
    if conversion["order"] == "lsb":
        words = words[::-1]
    # Modbus words are already integers; keep the high byte first within each word.
    raw = b"".join(word.to_bytes(2, "big") for word in words)
    value = struct.unpack(">" + fmt, raw)[0]
    if conversion["type"] == "bit":
        value = bool(value & (1 << conversion["bit"]))
    # Preserve exact 64-bit integers and booleans for identity transformations.
    if conversion["scale"] != 1 or conversion["offset"] != 0:
        value = value * conversion["scale"] + conversion["offset"]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"Non-finite value for {conversion['name']}")
    return value


def _read_registers(mbus, structure):
    """Read and convert all blocks, returning None if any read is incomplete."""
    data = {}
    for block in structure:
        regs = mbus.read_holding_registers(block["address"], block["length"])
        if regs is None or len(regs) != block["length"]:
            return None
        for conversion in block["convert"]:
            data[conversion["name"]] = _decode_registers(regs, conversion)
    return data


def _send_sensor_data_to_influxdb(db, value):

    json_body = [
        {
            "measurement": "MeasurementName",
            "tags": {
                "host": "HostName",
                "tag1": "Tag1Value"
            },
            "time": datetime.datetime.fromtimestamp(int(time.time())),
            "fields": value
        }
    ]

    db.write_points(json_body)


def _init_influxdb_database(db):
    databases = db.get_list_database()
    if len(list(filter(lambda x: x['name'] == INFLUXDB_DATABASE, databases))) == 0:
        db.create_database(INFLUXDB_DATABASE)
    db.switch_database(INFLUXDB_DATABASE)

def main():

        registers = _load_registers(REGISTERS_FILE)

        db = InfluxDBClient(INFLUXDB_HOST, INFLUXDB_PORT, INFLUXDB_USER, INFLUXDB_PASSWORD )

        _init_influxdb_database( db )
        print("Initialized db")

        mbus = ModbusClient(host=MODBUS_HOST, port=MODBUS_PORT, auto_open=True, debug=False)

        while True:
            data = _read_registers(mbus, registers)

            if data:
                print( data )
                _send_sensor_data_to_influxdb( db, data )

            else:
                print('unable to read registers')

            time.sleep(SLEEP_READOUT)

if __name__ == '__main__':
    print('MODBUS TCP to INFLUX DB')
    while True:
        try:
            main()
        except Exception as e:
            print(e)
            time.sleep(SLEEP_RETRY)
