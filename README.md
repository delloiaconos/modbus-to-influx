# ModBus to Influx

A Python gateway that polls a Modbus TCP device and writes scaled register values
to InfluxDB. It can run directly with Python or in a Docker container.

The gateway uses the database-based InfluxDB API exposed by the `influxdb` Python
client. Its configuration uses a username, password, and database; it does not
implement an InfluxDB bucket/token workflow.

## How it works

- Connects to InfluxDB, creates the configured database if it is missing, and
  selects it. The account needs permission to list databases, create the database
  when needed, and write points.
- Reads 50 holding registers starting at protocol address `2` from the Modbus
  device, then converts selected registers to floating-point fields.
- Writes one point per successful read with measurement `MeasurementName`, tags
  `host=HostName` and `tag1=Tag1Value`, and a timestamp from the gateway's clock.
- Waits `SLEEP_READOUT` seconds after each polling cycle. An empty or failed read
  logs `unable to read registers` and continues polling. An exception causes the
  gateway to wait `SLEEP_RETRY` seconds before initializing both clients again.

The register mapping, measurement name, and tags are hard-coded in
[`gateway/app.py`](gateway/app.py). Adapt them to your device and data model before
collecting data.

## Run with Python

Use Python 3 (the Dockerfile uses Python 3.12.1) and make sure the gateway can
reach both the Modbus device and InfluxDB.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt

export MODBUS_HOST=192.168.0.10
export INFLUXDB_HOST=172.16.1.10
export INFLUXDB_USER=influx_user
export INFLUXDB_PASSWORD='replace-with-your-password'
export INFLUXDB_DATABASE=influxdb

python gateway/app.py
```

Stop the process with `Ctrl+C`.

## Run with Docker

The [`Dockerfile`](Dockerfile) installs dependencies from
[`requirements.txt`](requirements.txt). Build the image from the repository root:

```sh
docker build -t modbus-to-influx .
```

Dependencies are not pinned; use versions validated in your environment for
reproducible deployments.

Create a local `gateway.env` file with your connection settings:

```dotenv
MODBUS_HOST=192.168.0.10
MODBUS_PORT=502
INFLUXDB_HOST=172.16.1.10
INFLUXDB_PORT=8086
INFLUXDB_USER=influx_user
INFLUXDB_PASSWORD=replace-with-your-password
INFLUXDB_DATABASE=influxdb
SLEEP_READOUT=5
SLEEP_RETRY=120
```

Keep credentials out of version control. Start the container and inspect its logs:

```sh
docker run -d --name modbus-to-influx \
  --env-file gateway.env \
  -e PYTHONUNBUFFERED=1 \
  modbus-to-influx
docker logs -f modbus-to-influx
```

Both configured hosts must be reachable from the container. No published ports
are required because the gateway initiates outbound connections. Stop it with
`docker stop modbus-to-influx`.

## Configuration

All settings are read from environment variables at startup.

| Variable | Default | Purpose |
| --- | --- | --- |
| `MODBUS_HOST` | `192.168.0.10` | Modbus TCP device address |
| `MODBUS_PORT` | `502` | Modbus TCP port |
| `INFLUXDB_HOST` | `172.16.1.10` | InfluxDB server address |
| `INFLUXDB_PORT` | `8086` | InfluxDB port |
| `INFLUXDB_USER` | `influx_user` | InfluxDB username |
| `INFLUXDB_PASSWORD` | `influx_pass` | InfluxDB password |
| `INFLUXDB_DATABASE` | `influxdb` | Database to create/select |
| `INFLUXDB_ORG` | `organization` | Read by the script but currently unused |
| `SLEEP_READOUT` | `5` | Delay between polling cycles, in seconds |
| `SLEEP_RETRY` | `120` | Delay before restarting after an exception, in seconds |

Ports and delays must be integers; delays must be nonnegative. The Modbus unit ID
is not explicitly configured by the script and uses the client's default.

## Register mapping

`REGISTERS` in `gateway/app.py` defines the read blocks, following the structure
used in `examples/main2.py`. Each block contains:

- `address`: the starting holding-register address.
- `length`: the number of 16-bit registers to read.
- `convert`: a list of `name` / `func` entries. Each function receives the entire
  block's register list and returns the value for its named InfluxDB field.

Add blocks or conversions to extend the mapping. Field names must be unique
across blocks, and conversion indexes are relative to the start of their block.
All blocks are read each cycle and their fields are combined into one point.
If any block read fails or returns an incomplete result, the cycle is skipped.
Conversion exceptions use the existing `SLEEP_RETRY` behavior.

The default structure retains the single read at address `2`, length `50`, and
all field names and scaling listed below.

Addresses below are zero-based Modbus protocol addresses, not `4xxxx` register
labels. Each field uses a single register from the returned block, divided by the
listed value; the script does not combine register pairs or decode signed values.

| Fields (in order) | Register addresses (in order) | Divide by |
| --- | --- | --- |
| `V1`, `V2`, `V3` | 3, 5, 7 | 100 |
| `I1`, `I2`, `I3` | 9, 11, 13 | 10000 |
| `U12`, `U23`, `U31` | 15, 17, 19 | 100 |
| `P1`, `P2`, `P3` | 21, 23, 25 | 100000 |
| `Q1`, `Q2`, `Q3` | 27, 29, 31 | 100000 |
| `S1`, `S2`, `S3` | 33, 35, 37 | 100000 |
| `phi1`, `phi2`, `phi3` | 39, 41, 43 | 10000 |
| `freq` | 51 | 1000 |

Confirm addresses, scaling, and units against your device's register map.

## License

See [LICENSE](LICENSE) for the GNU General Public License, version 3.
