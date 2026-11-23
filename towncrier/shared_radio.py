"""One MeshCore transport and command lane per running Towncrier server."""
import asyncio
import logging
import threading
import time
from contextlib import asynccontextmanager

from .radio import Radio

logger = logging.getLogger(__name__)


class SharedRadio:
    def __init__(self, store, connector=None):
        self.store = store
        self.connector = connector or self._open
        self.loop = asyncio.new_event_loop()
        self.lock = asyncio.Lock()
        self.lifecycle_lock = threading.Lock()
        self.closed = False
        self.device = None
        self.endpoint = None
        self.retry_at = 0
        self.requested = False
        self.room_sessions = {}
        self.operations = set()
        self.thread = threading.Thread(target=self._run, name="meshcore-owner", daemon=True)
        store.radio_owner = self
        self.thread.start()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.maintenance = self.loop.create_task(self._maintain())
        self.loop.run_forever()
        self.loop.close()

    @staticmethod
    async def _open(config):
        from meshcore import EventType, MeshCore
        from meshcore.serial_cx import SerialConnection
        from meshcore.tcp_cx import TCPConnection

        if config.transport == "tcp":
            if not config.host:
                raise ValueError("TCP companion host is required")
            connection = TCPConnection(config.host, config.tcp_port)
        else:
            connection = SerialConnection(config.port, config.baud)
        # Keep the object even if the handshake fails, so its dispatcher and
        # partially opened transport can be closed before another open.
        device = MeshCore(connection)
        try:
            response = await asyncio.wait_for(device.connect(), 12)
            if response is None or response.type != EventType.SELF_INFO:
                raise ConnectionError("MeshCore companion did not answer the handshake")
            return device
        except BaseException:
            try:
                await device.disconnect()
            finally:
                # ConnectionManager only closes a transport it marked connected.
                await connection.disconnect()
            raise

    async def _drop(self):
        self.room_sessions.clear()
        if self.device is not None:
            await self.device.disconnect()
            self.device = None

    async def _ensure(self):
        # Must be called with the command lock held.
        self.requested = True
        config = Radio.from_store(self.store)
        endpoint = ((config.transport, config.host, config.tcp_port) if config.transport == "tcp"
                    else (config.transport, config.port, config.baud))
        if endpoint != self.endpoint:
            await self._drop()
            self.endpoint = endpoint
            self.retry_at = 0
        if self.device is not None and not self.device.is_connected:
            await self._drop()
        if self.device is None:
            if time.monotonic() < self.retry_at:
                raise ConnectionError("MeshCore companion unavailable; reconnect pending")
            try:
                self.device = await self.connector(config)
            except Exception:
                self.retry_at = time.monotonic() + 2
                raise
        return self.device

    async def _maintain(self):
        while True:
            await asyncio.sleep(.25)
            if self.requested:
                try:
                    async with self.lock:
                        await self._ensure()
                except Exception as exc:
                    logger.debug("MeshCore reconnect pending: %s", exc)

    @asynccontextmanager
    async def connection(self):
        async with self.lock:
            device = await self._ensure()
            try:
                yield device
            except OSError:
                await self._drop()
                raise

    async def run_bot_command(self, device, name, *args):
        async with self.lock:
            if await self._ensure() is not device:
                raise ConnectionError("MeshCore connection changed")
            command = asyncio.create_task(getattr(device.commands, name)(*args))
            try:
                return await asyncio.shield(command)
            except asyncio.CancelledError:
                # A bot reconfiguration must not release the lane with an old
                # command still waiting for the companion's response.
                await asyncio.gather(command, return_exceptions=True)
                raise

    async def drop_if_current(self, device):
        async with self.lock:
            if device is self.device:
                await self._drop()

    def run(self, operation, *args):
        """Run a coroutine on the owner loop and wait from an HTTP/worker thread."""
        async def invoke():
            task = asyncio.current_task()
            self.operations.add(task)
            try:
                return await operation(*args)
            finally:
                self.operations.remove(task)

        with self.lifecycle_lock:
            if self.closed:
                raise ConnectionError("MeshCore owner is shutting down")
            future = asyncio.run_coroutine_threadsafe(invoke(), self.loop)
        return future.result()

    def call(self, operation, *args):
        async def command():
            async with self.connection() as device:
                return await operation(device, *args)
        return self.run(command)

    def close(self):
        async def shutdown():
            tasks = {*self.operations, self.maintenance}
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._drop()

        with self.lifecycle_lock:
            if self.closed:
                return
            self.closed = True
            future = asyncio.run_coroutine_threadsafe(shutdown(), self.loop)
        try:
            future.result()
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join()
