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
            regs = mbus.read_holding_registers(2, 50)
              
            if regs:
                print( regs )
                data = { 'V1' : float( regs[1]/100), 
                         'V2' : float( regs[3]/100),
                         'V3' : float( regs[5]/100),
                         'I1' : float( regs[7]/10000),
                         'I2' : float( regs[9]/10000),
                         'I3' : float( regs[11]/10000),
                         'U12' : float( regs[13]/100),
                         'U23' : float( regs[15]/100),
                         'U31' : float( regs[17]/100),
                         'P1' : float( regs[19]/100000),
                         'P2' : float( regs[21]/100000),
                         'P3' : float( regs[23]/100000),
                         'Q1' : float( regs[25]/100000),
                         'Q2' : float( regs[27]/100000),
                         'Q3' : float( regs[29]/100000),
                         'S1' : float( regs[31]/100000),
                         'S2' : float( regs[33]/100000),
                         'S3' : float( regs[35]/100000),
                         'phi1' : float( regs[37]/10000),
                         'phi2' : float( regs[39]/10000),
                         'phi3' : float( regs[41]/10000),
                         'freq' : float( regs[49]/1000) }
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

