FROM python:3.12.1-alpine3.18
MAINTAINER Salvatore Dello Iacono

# Override with docker run -e or --env-file.
ENV MODBUS_HOST=192.168.0.10
ENV MODBUS_PORT=502
ENV INFLUXDB_HOST=172.16.1.10
ENV INFLUXDB_PORT=8086
ENV INFLUXDB_USER=influx_user
ENV INFLUXDB_PASSWORD=influx_pass
ENV INFLUXDB_DATABASE=influxdb
ENV SLEEP_READOUT=5
ENV SLEEP_RETRY=120
ENV CLIENT_TIMEOUT=10
ENV REGISTERS_FILE=/app/registers.json

WORKDIR /app
COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt

COPY gateway /app/
COPY registers.json /app/registers.json

CMD ["python", "app.py"]
