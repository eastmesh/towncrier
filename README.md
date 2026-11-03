# Towncrier

Towncrier schedules messages to MeshCore **channels** through a USB-connected companion radio. It is a small Python service with a local browser configuration page.

## Install

On a Linux host with the radio connected:

```bash
git clone https://github.com/eastmesh/towncrier.git
cd towncrier
./install.sh
```

The installer checks Linux, Python 3.10+, pip, and venv; creates `/opt/towncrier`, installs the `meshcore` dependency, and installs a systemd unit. It does not start or enable the service.

Then open `http://127.0.0.1:8080/`, set the serial device (usually `/dev/ttyUSB0` or `/dev/ttyACM0`) and baud rate, and add schedules using the MeshCore channel index. Each interval is in seconds and must be at least 60.

## Service

```bash
sudo systemctl enable --now towncrier
systemctl status towncrier
journalctl -u towncrier -f
```

Add the service user to the appropriate serial group if needed (`dialout` on many distributions), then restart the service:

```bash
sudo usermod -aG dialout towncrier
sudo systemctl restart towncrier
```

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q towncrier
.venv/bin/python -m towncrier --db /tmp/towncrier.db
```

The UI binds to loopback by default and has no authentication. Do not expose it directly to a network. The first version supports USB serial only; BLE, TCP, multi-user auth, and cloud hosting are intentionally out of scope. A failed radio send records an error and retries on the next scheduler pass without advancing the schedule.
