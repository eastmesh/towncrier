import asyncio
import hashlib
import threading

class Radio:
    _lock = threading.Lock()
    def __init__(self, port, baud, transport="serial", host="", tcp_port=4000):
        self.port, self.baud = port, int(baud)
        self.transport = str(transport or "serial").strip().lower()
        self.host = str(host or "").strip()
        self.tcp_port = int(tcp_port)
        if self.transport not in {"serial", "tcp"}: raise ValueError("connection type must be serial or tcp")
    @classmethod
    def from_store(cls, store):
        return cls(
            store.setting("port", "/dev/ttyUSB0"),
            store.setting("baud", "115200"),
            transport=store.setting("connection_type", "serial"),
            host=store.setting("tcp_host", ""),
            tcp_port=store.setting("tcp_port", "4000"),
        )
    async def _connect(self):
        from meshcore import MeshCore
        if self.transport == "tcp":
            if not self.host: raise ValueError("TCP companion host is required")
            if not 1 <= self.tcp_port <= 65535: raise ValueError("TCP port must be between 1 and 65535")
            mc = await MeshCore.create_tcp(self.host, self.tcp_port)
        else:
            if not self.port: raise ValueError("serial companion port is required")
            mc = await MeshCore.create_serial(self.port, self.baud)
        if mc is None:
            raise RuntimeError("TCP companion did not respond" if self.transport == "tcp" else "radio did not respond as a MeshCore serial companion")
        return mc
    async def _send(self, channel, message):
        from meshcore import EventType
        mc = await self._connect()
        try:
            result = await mc.commands.send_chan_msg(channel, message)
            if result.type == EventType.ERROR: raise RuntimeError(result.payload)
        finally: await mc.disconnect()
    async def _snapshot(self):
        from meshcore import EventType
        mc = await self._connect(); result=[]
        try:
            info = {key: mc.self_info.get(key) for key in ("radio_freq", "radio_bw", "radio_sf", "radio_cr")}
            device = await mc.commands.send_device_query()
            limit = int(device.payload.get("max_channels", 40)) if device.type == EventType.DEVICE_INFO else 40
            for index in range(max(1, min(limit, 256))):
                event = await mc.commands.get_channel(index)
                if event.type == EventType.ERROR: continue
                channel = event.payload
                name = channel.get("channel_name", "").strip()
                secret = channel.get("channel_secret", b"")
                result.append({"index": index, "name": name, "configured": bool(name or secret.strip(b"\\x00"))})
            return info, result
        finally: await mc.disconnect()
    async def _radio_settings(self):
        return (await self._snapshot())[0]
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
        return (await self._snapshot())[1]
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
    async def _publisher_contacts(self):
        from meshcore import EventType
        mc = await self._connect()
        try:
            result = await mc.commands.get_contacts()
            if result.type == EventType.ERROR:
                raise RuntimeError(result.payload)
            rooms = []
            for contact in mc.contacts.values():
                if contact.get("type") != 3:
                    continue
                public_key = str(contact.get("public_key", "")).lower()
                if len(public_key) != 64:
                    continue
                rooms.append({
                    "publicKeyHex": public_key,
                    "name": contact.get("adv_name") or "Unnamed room server",
                })
            return rooms
        finally:
            await mc.disconnect()

    async def _publisher_login(self, public_key, password):
        from meshcore import EventType
        key = str(public_key).strip().lower()
        if len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
            raise ValueError("Room-server public key must be 64 hexadecimal characters.")
        if not isinstance(password, str) or not password:
            raise ValueError("Enter the room password.")
        if len(password.encode("utf-8")) > 15:
            raise ValueError("Room password cannot exceed 15 UTF-8 bytes.")
        mc = await self._connect()
        try:
            contacts_result = await mc.commands.get_contacts()
            if contacts_result.type == EventType.ERROR:
                raise RuntimeError(contacts_result.payload)
            contact = next((item for item in mc.contacts.values()
                            if item.get("type") == 3
                            and str(item.get("public_key", "")).lower() == key), None)
            if contact is None:
                # Manual-key entry is an explicit user-supplied destination; the Python client accepts a full hex key.
                contact = key
            loop = asyncio.get_running_loop()
            logged_in = loop.create_future()
            prefix = key[:12]
            def on_login(event):
                event_prefix = (getattr(event, "attributes", None) or {}).get("pubkey_prefix")
                if (event_prefix is None or event_prefix == prefix) and not logged_in.done():
                    logged_in.set_result(event)
            subscriptions = [
                mc.dispatcher.subscribe(EventType.LOGIN_SUCCESS, on_login),
                mc.dispatcher.subscribe(EventType.LOGIN_FAILED, on_login),
            ]
            try:
                sent = await mc.commands.send_login(contact, password)
                if sent is None or sent.type == EventType.ERROR:
                    raise RuntimeError(f"Room login request failed: {getattr(sent, 'payload', None)}")
                timeout = max(2.0, float(sent.payload.get("suggested_timeout", 8000)) / 800.0 + 2.0)
                try:
                    result = await asyncio.wait_for(logged_in, timeout)
                except asyncio.TimeoutError as exc:
                    raise RuntimeError("Room login failed or timed out; check the key, password, and radio reachability.") from exc
                if result.type != EventType.LOGIN_SUCCESS:
                    raise RuntimeError("Room login was rejected by the server.")
                return result.payload if isinstance(result.payload, dict) else {"permissions": result.payload}
            finally:
                for subscription in subscriptions:
                    subscription.unsubscribe()
        finally:
            await mc.disconnect()

    async def _publisher_send(self, public_key, text):
        from meshcore import EventType
        key = str(public_key).strip().lower()
        if len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
            raise ValueError("Room-server public key must be 64 hexadecimal characters.")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Message cannot be empty.")
        if len(text.encode("utf-8")) > 151:
            raise ValueError("Message exceeds the room-server limit of 151 UTF-8 bytes.")
        mc = await self._connect()
        try:
            result = await mc.commands.send_msg(key, text)
            if result.type == EventType.ERROR:
                raise RuntimeError(result.payload)
            return {"sent": True}
        finally:
            await mc.disconnect()

    def publisher_contacts(self):
        with self._lock: return asyncio.run(self._publisher_contacts())

    def publisher_login(self, public_key, password):
        with self._lock: return asyncio.run(self._publisher_login(public_key, password))

    def publisher_send(self, public_key, text):
        with self._lock: return asyncio.run(self._publisher_send(public_key, text))

    def send(self, channel, message):
        with self._lock: asyncio.run(self._send(channel, message))
    def snapshot(self):
        with self._lock: return asyncio.run(self._snapshot())
    def radio_settings(self):
        with self._lock: return asyncio.run(self._radio_settings())
    def set_radio(self, freq, bw, sf, cr):
        with self._lock: return asyncio.run(self._set_radio(freq, bw, sf, cr))
    def channels(self):
        with self._lock: return asyncio.run(self._channels())
    def set_channel(self, index, name, secret=None):
        with self._lock: asyncio.run(self._set_channel(index, name, secret))
    def delete_channel(self, index): self.set_channel(index, "", b"\x00" * 16)
