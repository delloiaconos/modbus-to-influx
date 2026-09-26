#!/usr/bin/env python3

import time
import datetime
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


# Addresses and lengths are in 16-bit holding registers. Conversion functions
# receive the complete register list for their block (indexes are block-relative).
REGISTERS = [
    {
        "address": 2,
        "length": 50,
        "convert": [
            {"name": "V1", "func": lambda regs: float(regs[1] / 100)},
            {"name": "V2", "func": lambda regs: float(regs[3] / 100)},
            {"name": "V3", "func": lambda regs: float(regs[5] / 100)},
            {"name": "I1", "func": lambda regs: float(regs[7] / 10000)},
            {"name": "I2", "func": lambda regs: float(regs[9] / 10000)},
            {"name": "I3", "func": lambda regs: float(regs[11] / 10000)},
            {"name": "U12", "func": lambda regs: float(regs[13] / 100)},
            {"name": "U23", "func": lambda regs: float(regs[15] / 100)},
            {"name": "U31", "func": lambda regs: float(regs[17] / 100)},
            {"name": "P1", "func": lambda regs: float(regs[19] / 100000)},
            {"name": "P2", "func": lambda regs: float(regs[21] / 100000)},
            {"name": "P3", "func": lambda regs: float(regs[23] / 100000)},
            {"name": "Q1", "func": lambda regs: float(regs[25] / 100000)},
            {"name": "Q2", "func": lambda regs: float(regs[27] / 100000)},
            {"name": "Q3", "func": lambda regs: float(regs[29] / 100000)},
            {"name": "S1", "func": lambda regs: float(regs[31] / 100000)},
            {"name": "S2", "func": lambda regs: float(regs[33] / 100000)},
            {"name": "S3", "func": lambda regs: float(regs[35] / 100000)},
            {"name": "phi1", "func": lambda regs: float(regs[37] / 10000)},
            {"name": "phi2", "func": lambda regs: float(regs[39] / 10000)},
            {"name": "phi3", "func": lambda regs: float(regs[41] / 10000)},
            {"name": "freq", "func": lambda regs: float(regs[49] / 1000)},
        ],
    },
]


def _read_registers(mbus, structure):
    """Read and convert all blocks, returning None if any read is incomplete."""
    data = {}
    for block in structure:
        regs = mbus.read_holding_registers(block["address"], block["length"])
        if regs is None or len(regs) != block["length"]:
            return None
        for conversion in block["convert"]:
            data[conversion["name"]] = conversion["func"](regs)
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

        db = InfluxDBClient(INFLUXDB_HOST, INFLUXDB_PORT, INFLUXDB_USER, INFLUXDB_PASSWORD )

        _init_influxdb_database( db )
        print("Initialized db")

        mbus = ModbusClient(host=MODBUS_HOST, port=MODBUS_PORT, auto_open=True, debug=False)

        while True:
            data = _read_registers(mbus, REGISTERS)

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
