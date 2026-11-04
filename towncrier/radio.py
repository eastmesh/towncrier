import asyncio
import hashlib
import threading

class Radio:
    _lock = threading.Lock()
    def __init__(self, port, baud): self.port, self.baud = port, int(baud)
    async def _connect(self):
        from meshcore import MeshCore
        mc = await MeshCore.create_serial(self.port, self.baud)
        if mc is None: raise RuntimeError("radio did not respond as a MeshCore serial companion")
        return mc
    async def _send(self, channel, message):
        from meshcore import EventType
        mc = await self._connect()
        try:
            result = await mc.commands.send_chan_msg(channel, message)
            if result.type == EventType.ERROR: raise RuntimeError(result.payload)
        finally: await mc.disconnect()
    async def _radio_settings(self):
        mc = await self._connect()
        try:
            return {key: mc.self_info.get(key) for key in ("radio_freq", "radio_bw", "radio_sf", "radio_cr")}
        finally: await mc.disconnect()
    async def _set_radio(self, freq, bw, sf, cr):
        from meshcore import EventType
        mc = await self._connect()
        try:
            result = await mc.commands.set_radio(freq, bw, sf, cr)
            if result.type == EventType.ERROR and result.payload.get("reason") != "no_event_received":
                raise RuntimeError(result.payload)
        finally: await mc.disconnect()
        await asyncio.sleep(1)
        settings = await self._radio_settings()
        expected = {"radio_freq": freq, "radio_bw": bw, "radio_sf": sf, "radio_cr": cr}
        if any(settings.get(key) != value for key, value in expected.items()):
            raise RuntimeError("radio did not confirm the radio settings")
        return settings
    async def _channels(self):
        from meshcore import EventType
        mc=await self._connect(); result=[]
        try:
            limit=40
            info=await mc.commands.send_device_query()
            if info.type==EventType.DEVICE_INFO:
                limit=int(info.payload.get("max_channels",limit))
            limit=max(1,min(limit,256))
            for index in range(limit):
                event = await mc.commands.get_channel(index)
                if event.type == EventType.ERROR: continue
                channel = event.payload
                name = channel.get("channel_name", "").strip()
                secret = channel.get("channel_secret", b"")
                if len(secret) != 16: secret = b"\x00" * 16
                result.append({"index":index,"name":name,"configured":bool(name or secret.strip(b"\x00"))})
            return result
        finally: await mc.disconnect()
    async def _set_channel(self, index, name, secret):
        from meshcore import EventType
        mc = await self._connect()
        try:
            result = await mc.commands.set_channel(index, name, secret)
            if result.type == EventType.ERROR and result.payload.get("reason") != "no_event_received":
                raise RuntimeError(result.payload)
        finally: await mc.disconnect()
        await asyncio.sleep(1)
        mc = await self._connect()
        try:
            event = await mc.commands.get_channel(index)
            if event.type == EventType.ERROR: raise RuntimeError(event.payload)
            channel = event.payload
            expected = hashlib.sha256(name.encode()).digest()[:16] if name.startswith("#") or secret is None else secret
            if channel.get("channel_name") != name or channel.get("channel_secret") != expected:
                raise RuntimeError("radio did not confirm the channel change")
        finally: await mc.disconnect()
    def send(self, channel, message):
        with self._lock: asyncio.run(self._send(channel, message))
    def radio_settings(self):
        with self._lock: return asyncio.run(self._radio_settings())
    def set_radio(self, freq, bw, sf, cr):
        with self._lock: return asyncio.run(self._set_radio(freq, bw, sf, cr))
    def channels(self):
        with self._lock: return asyncio.run(self._channels())
    def set_channel(self, index, name, secret=None):
        with self._lock: asyncio.run(self._set_channel(index, name, secret))
    def delete_channel(self, index): self.set_channel(index, "", b"\x00" * 16)
