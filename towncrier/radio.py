import asyncio
import threading

class Radio:
    _lock = threading.Lock()
    def __init__(self, port, baud): self.port, self.baud = port, int(baud)
    async def _connect(self):
        from meshcore import MeshCore
        return await MeshCore.create_serial(self.port, self.baud)
    async def _send(self, channel, message):
        from meshcore import EventType
        mc = await self._connect()
        try:
            result = await mc.commands.send_chan_msg(channel, message)
            if result.type == EventType.ERROR: raise RuntimeError(result.payload)
        finally: await mc.disconnect()
    async def _channels(self):
        from meshcore import EventType
        mc = await self._connect(); result = []
        try:
            for index in range(40):
                event = await mc.commands.get_channel(index)
                if event.type == EventType.ERROR: continue
                channel = event.payload
                name = channel.get("channel_name", "").strip()
                secret = channel.get("channel_secret", b"")
                if name or secret.strip(b"\x00"):
                    result.append({"index": index, "name": name})
            return result
        finally: await mc.disconnect()
    async def _set_channel(self, index, name, secret):
        from meshcore import EventType
        mc = await self._connect()
        try:
            result = await mc.commands.set_channel(index, name, secret)
            if result.type == EventType.ERROR: raise RuntimeError(result.payload)
        finally: await mc.disconnect()
    def send(self, channel, message):
        with self._lock: asyncio.run(self._send(channel, message))
    def channels(self):
        with self._lock: return asyncio.run(self._channels())
    def set_channel(self, index, name, secret=None):
        with self._lock: asyncio.run(self._set_channel(index, name, secret))
    def delete_channel(self, index): self.set_channel(index, "", b"\x00" * 16)
