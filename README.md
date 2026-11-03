# Towncrier

Towncrier schedules messages to MeshCore **channels** through a USB-connected companion radio. It runs as a Linux background service and has a simple web page for setup.

This guide assumes this is your first time using a Linux desktop and a terminal.

## What you need

- A Linux computer with a desktop and a web browser.
- A MeshCore companion radio connected with a USB cable.
- An internet connection during installation so Python can download `meshcore`.
- An account that is allowed to use `sudo` (the installer needs administrator permission).

The web page is deliberately local-only and has no login. Do not expose it to the internet or bind it to a public network address.

## 1. Open a terminal

A terminal is a window where you type commands. On most Linux desktops, press `Ctrl` + `Alt` + `T`. You can also open the application menu, search for **Terminal**, and click it.

Commands below are typed into that window. Press **Enter** after each command. When Linux asks for your password, type the password you use to log in; the characters will not appear while you type. Press **Enter** when finished.

## 2. Download Towncrier

In the terminal, run:

```bash
git clone https://github.com/eastmesh/towncrier.git
cd towncrier
```

If `git` is not installed, your distribution may offer an **Install** button in the terminal error. On Debian or Ubuntu, an administrator can install it with:

```bash
sudo apt update
sudo apt install git python3 python3-venv python3-pip
```

## 3. Install and start the service

Make sure the USB radio is connected, then run:

```bash
./install.sh
```

The installer:

1. Checks that the computer is Linux and has Python 3.10 or newer, pip, venv, and systemd.
2. Creates the system account `towncrier` if it does not already exist.
3. Adds that account to the `dialout` serial-device group when the group exists.
4. Installs Towncrier under `/opt/towncrier`.
5. Creates `/var/lib/towncrier` for the database and gives it to the service account.
6. Installs the `meshcore` Python library.
7. Installs the systemd service, enables it to start at boot, and starts it now.

When it finishes, open your normal web browser (Firefox, Chrome, Chromium, or similar) and enter:

```text
http://127.0.0.1:8080/
```

`127.0.0.1` means “this computer”. It does not mean a website on the public internet.

## 4. Find the USB serial device

The radio usually appears as `/dev/ttyUSB0` or `/dev/ttyACM0`. To see possible device names, return to the terminal and run:

```bash
ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
```

If you see one device, enter that exact name in the **Serial port** box on the Towncrier page. If you see no device:

- Check that the USB cable carries data, not only power.
- Unplug and reconnect the radio.
- Try another USB port.
- Run the `ls` command again.
- Check the service log with `sudo journalctl -u towncrier -n 50`.

The installer adds `towncrier` to `dialout`. If the service still cannot open the device, restart the service after installation with:

```bash
sudo systemctl restart towncrier
```

## 5. Configure the radio

On the Towncrier page, the **USB companion** section contains:

- **Serial port**: the device path, for example `/dev/ttyUSB0`.
- **Baud**: normally `115200`. Only change this if your companion radio documentation specifies another value.

Click **Save settings**. The settings are stored in `/var/lib/towncrier/towncrier.db` and remain after a reboot.

## 6. Add a scheduled message

Use the **Add schedule** form:

- **Name**: a short label you will recognise, such as `Morning status`.
- **Channel index**: the numeric MeshCore channel slot, from `0` to `255`. This is the channel index configured on the companion radio, not the channel name.
- **Interval seconds**: how often to send the message. The smallest allowed value is `60`, meaning once per minute. For once per hour, enter `3600`.
- **Message**: the text to send. Messages may contain up to 500 characters.
- **Enabled**: leave checked for the scheduler to send it. Uncheck it when you want to keep the message without sending it.

Click **Save schedule**. The schedule is immediately stored. The first send is due as soon as the scheduler sees it; later sends use the interval you entered.

The schedule table shows whether an item is enabled, its last status, and the most recent error. The available actions are:

- **edit**: load the schedule into the form.
- **toggle**: enable or disable it.
- **send now**: send one copy immediately without changing its normal timer.
- **delete**: permanently remove it.

A failed radio send is recorded as an error. Towncrier does not advance the timer after a failed send, so it will try again rather than silently losing the message.

## 7. Check or control the service

To see whether it is running:

```bash
systemctl status towncrier
```

Press `q` to leave the status screen.

To watch recent messages and errors:

```bash
sudo journalctl -u towncrier -f
```

Press `Ctrl` + `C` to stop watching the log.

Useful controls:

```bash
sudo systemctl restart towncrier   # restart after changing hardware or settings
sudo systemctl stop towncrier      # stop sending and stop the web page
sudo systemctl start towncrier     # start it again
sudo systemctl disable towncrier   # do not start automatically at boot
sudo systemctl enable towncrier    # start automatically at boot
```

## Troubleshooting

### The page does not open

Run:

```bash
systemctl status towncrier
sudo journalctl -u towncrier -n 50
```

If the service is stopped, run `sudo systemctl restart towncrier` and try `http://127.0.0.1:8080/` again. Another program may already be using port 8080; the log will show the error.

### The page opens but messages are not sent

Check the serial port spelling, baud rate, radio power, companion firmware, and channel index. Use **send now** once while watching the log. A successful send changes the schedule status to `sent`; a failed send shows `error` and the reason in the table/log.

### The radio device changes name after reboot

Some Linux systems assign a different `/dev/ttyUSB*` number when several USB serial devices are present. Reconnect the radio, run the `ls` command again, update the Serial port field, and save it.

### I want to remove Towncrier

Stop and disable it first:

```bash
sudo systemctl disable --now towncrier
sudo rm /etc/systemd/system/towncrier.service
sudo systemctl daemon-reload
sudo rm -rf /opt/towncrier
```

The schedules database is kept at `/var/lib/towncrier/towncrier.db` until you remove it explicitly.

## Development and tests

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q towncrier
.venv/bin/python -m towncrier --db /tmp/towncrier.db
```

The first version supports USB serial only. BLE, TCP, multi-user authentication, cloud hosting, and public network exposure are intentionally out of scope.
