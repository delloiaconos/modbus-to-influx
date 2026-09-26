# ModBus to Influx

A Python gateway that polls a Modbus TCP device and writes scaled register values to InfluxDB.
It can run directly with Python or in a Docker container.

The gateway uses the database-based InfluxDB API exposed by the `influxdb` Python client.
Its configuration uses a username, password, and database; it does not implement an InfluxDB bucket/token workflow.

## How it works

- Connects to InfluxDB, creates the configured database if it is missing, and selects it.
  The account needs permission to list databases, create the database when needed, and write points.
- Reads the configured holding-register blocks from the Modbus device and decodes their fields using the specified types, word order, and scaling.
- Writes one point per block using its configured measurement and tags, with a shared UTC timestamp from the gateway's clock.
- Checks InfluxDB with a startup ping and database query, then checks each write's result.
  Opens the Modbus connection explicitly before polling and checks reads.
- Waits `SLEEP_READOUT` seconds after a successful polling cycle.
  Failed opens, incomplete reads, rejected writes, and other exceptions close both clients and trigger a new session after `SLEEP_RETRY` seconds.
  Errors are logged with context.
- Uses a 10-second timeout for client requests and one InfluxDB client attempt; session retries are handled by the gateway.
  Ctrl+C also closes both clients.

The register mapping, measurement names, and tags are configured in [`registers.json`](registers.json).
Adapt them to your device and data model before collecting data.

## Run with Python

Use Python 3 (the Dockerfile uses Python 3.12) and make sure the gateway can reach both the Modbus device and InfluxDB.

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

The [`Dockerfile`](Dockerfile) installs dependencies from [`requirements.txt`](requirements.txt).
Build the image from the repository root:

```sh
docker build --pull -t modbus-to-influx .
```

The image uses `python:3.12-alpine` and runs as UID/GID `10001:10001`.
`PYTHONUNBUFFERED=1` is set in the image so stdout/stderr are unbuffered; `PYTHONDONTWRITEBYTECODE=1` prevents runtime bytecode writes.
Dependencies are installed without retaining pip's download cache.

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
CLIENT_TIMEOUT=10
```

Keep credentials out of version control.
Start the container and inspect its logs:

```sh
docker run -d --name modbus-to-influx \
  --env-file gateway.env \
  modbus-to-influx
docker logs -f modbus-to-influx
```

Both configured hosts must be reachable from the container.
No published ports are required because the gateway initiates outbound connections.
Stop it with `docker stop modbus-to-influx`.
The image uses `SIGINT` to trigger the app's client cleanup.
Any bind-mounted register file must be readable by UID `10001`.

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
| `SLEEP_READOUT` | `5` | Delay between polling cycles, in seconds |
| `SLEEP_RETRY` | `120` | Delay before restarting after an exception, in seconds |
| `CLIENT_TIMEOUT` | `10` | Modbus and InfluxDB clients timeout |
| `REGISTERS_FILE` | Repository-root `registers.json`; `/app/registers.json` in Docker | Path to the register configuration |

Ports and delays must be integers; delays must be nonnegative.
The Modbus unit ID is not explicitly configured by the script and uses the client's default.

## Register structure

[`registers.json`](registers.json) contains an example of nonempty JSON array of register blocks.
Each block describes a contiguous Modbus read and its destination in InfluxDB.
All block properties below are required.

| Block property | Type | Description |
| --- | --- | --- |
| `address` | Integer | Starting holding-register address, from 0 to 65535. Uses zero-based protocol addresses, not `4xxxx` labels. |
| `length` | Integer | Number of 16-bit registers to read, from 1 to 125. The complete block must fit within the address range. |
| `measurement` | String | Nonempty InfluxDB measurement name for the block. |
| `tags` | Array | Tag definitions; use an empty array for no tags. |
| `convert` | Array | Nonempty list of field conversion definitions. |

Each object in `tags` requires a nonempty string `name` and a nonempty string `value`.
Tag names must be unique within the block.

Each object in `convert` defines one output field.
The following properties are required, except `bit` (required only for the `bit` type)
and `byte` (optional, and allowed only for byte-sized types).

| Conversion property | Type | Description |
| --- | --- | --- |
| `name` | String | Nonempty InfluxDB field name. Must be unique within a measurement/tag set, including across blocks sharing that set. |
| `index` | Integer | Zero-based starting register within the block. All registers needed by the type must fit in the block. |
| `type` | String | One of the supported data types listed below. |
| `order` | String | `msb` for most significant 16-bit word first, or `lsb` for least significant word first. |
| `scale` | Number | Finite multiplier applied to the decoded value. Zero and negative values are allowed. |
| `offset` | Number | Finite value added after scaling. |
| `bit` | Integer | For `type: "bit"` only: bit position from 0 (least significant) to 15 (most significant). |
| `byte` | String | For `char`, `byte`, `uint8`, or `int8` only: `high` selects bits 8–15; `low` selects bits 0–7. Defaults to `low`. |

Different measurements or tag sets can reuse field names.
Unknown properties are rejected.
Bytes within each register remain high-byte first; word order has no effect on single-register values.

Numeric transformations use `value = decoded_value * scale + offset`.

| Type | Registers | Decoding |
| --- | --- | --- |
| `float32`, `float64` | 2, 4 | IEEE 754 single/double precision |
| `int16`, `int32`, `int64` | 1, 2, 4 | Signed two's-complement integer |
| `uint16`, `uint32`, `uint64` | 1, 2, 4 | Unsigned integer |
| `bit` | 1 | Selected bit of a holding register |
| `uint8`, `byte` | 1 | Selected byte as an unsigned integer (0–255); `byte` is an alias for `uint8` |
| `int8` | 1 | Selected byte as a signed two's-complement integer (−128–127) |
| `char` | 1 | Selected byte as a single Latin-1 character |

The `bit` type extracts a bit from a holding register, rather than reading a Modbus coil.
With scale `1` and offset `0`, the result is a boolean.
Other scales or offsets transform its numeric value (`0` or `1`).

Byte-sized types still read a 16-bit register; `index` remains a register index.
Use different `byte` selectors to define two fields from the same register.
`order` continues to control word ordering, so it does not affect byte selection.
Numeric byte types support the same scale and offset transformations as larger integers.
`char` returns a one-character string, including NUL for byte zero, and requires
scale `1` and offset `0`. It represents a single Latin-1 byte, not a multi-byte
UTF-8 character or a string spanning multiple registers.

Integer decoding preserves 64-bit precision; an identity transform preserves the integer type even when scale/offset are written as `1.0`/`0.0`.
Fractional scaling uses floating-point arithmetic.
NaN and infinite decoded results are rejected.

The gateway loads and validates the file before connecting, and reloads it when initializing again after an exception.
Restart the gateway to apply edits during normal operation.
Missing files and invalid configurations use the existing `SLEEP_RETRY` delay.
Python expressions and S7 conversion functions are not supported in this Modbus configuration.

The default file path is resolved relative to the application, independently of the working directory.
Override it with `REGISTERS_FILE`.
The Docker image includes the file; to supply a different mapping without rebuilding, add `--mount type=bind,src="$(pwd)/registers.json",dst=/app/registers.json,readonly` to the `docker run` command.

All blocks are read each cycle and sent in one batch, with one point per block and a shared timestamp.
If any block read fails or returns an incomplete result, the entire cycle is skipped.

## License

See [LICENSE](LICENSE) for the GNU General Public License, version 3.
