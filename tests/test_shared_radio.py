import asyncio
import threading
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from towncrier.db import Store
from towncrier.engine import BotListener, Scheduler
from towncrier.radio import PublisherSession, PublisherSessionExpired, Radio


class SharedRadioTests(unittest.TestCase):
    def test_concurrent_callers_share_one_connection_and_reconnect(self):
        from towncrier.shared_radio import SharedRadio

        devices = []
        entered = threading.Event()
        second_queued = threading.Event()
        release = threading.Event()
        seen = []

        async def connect(config):
            self.assertFalse(any(device.is_connected for device in devices))
            device = SimpleNamespace(is_connected=True)

            async def disconnect():
                device.is_connected = False

            device.disconnect = disconnect
            devices.append(device)
            return device

        async def first(device):
            seen.append(device)
            entered.set()
            while not release.is_set():
                await asyncio.sleep(.005)

        async def second(device):
            self.assertTrue(release.is_set())
            seen.append(device)

        async def queue_second(owner):
            async with owner.connection() as device:
                await second(device)

        async def check_blocked(owner):
            second_task = asyncio.create_task(queue_second(owner))
            await asyncio.sleep(0)
            try:
                self.assertFalse(second_task.done())
            finally:
                second_queued.set()
            await second_task

        store = SimpleNamespace(setting=lambda key, default: default)
        with SharedRadio(store, connector=connect) as owner, ThreadPoolExecutor(2) as pool:
            one = pool.submit(owner.call, first)
            try:
                self.assertTrue(entered.wait(2))
                two = pool.submit(owner.run, check_blocked, owner)
                self.assertTrue(second_queued.wait(2))
            finally:
                release.set()
            one.result(2)
            two.result(2)
            self.assertEqual(seen, [devices[0], devices[0]])
            devices[0].is_connected = False
            owner.call(second)
            self.assertEqual(len(devices), 2)
            self.assertIs(seen[-1], devices[1])
            self.assertIs(Radio.from_store(store).owner, owner)
        self.assertFalse(devices[-1].is_connected)

    def test_bot_scheduler_publisher_and_snapshot_use_the_same_live_companion(self):
        from towncrier.shared_radio import SharedRadio
        from test_bot_recovery import Companion, EVENTS, result

        events = SimpleNamespace(**vars(EVENTS))
        for name in ('LOGIN_SUCCESS', 'LOGIN_FAILED', 'DEVICE_INFO'):
            setattr(events, name, name)
        devices = []
        key = 'a' * 64
        other = 'b' * 64

        class Device(Companion):
            self_info = {'radio_freq': 915.8}
            contacts = {}

            def __init__(self):
                super().__init__()
                self.room_posts = []
                self.dispatcher = SimpleNamespace(subscribe=self.subscribe_login)

            def subscribe_login(self, kind, callback):
                self.subscribe(kind, callback)
                return SimpleNamespace(unsubscribe=lambda: self.unsubscribe(kind))

            async def get_msg(self):
                if self.started.is_set() and not self.responses:
                    return result(events.NO_MORE_MSGS)
                return await super().get_msg()

            async def get_contacts(self):
                return result(events.OK)

            async def send_login(self, room, password):
                event = result(events.LOGIN_SUCCESS, permissions=2)
                event.attributes = {'pubkey_prefix': room[:12]}
                self.callbacks[events.LOGIN_SUCCESS](event)
                return result(events.OK)

            async def send_msg(self, room, text):
                self.room_posts.append((room, text))
                return result(events.OK)

            async def send_device_query(self):
                return result(events.DEVICE_INFO, max_channels=1)

            async def get_channel(self, index):
                return result(events.OK, channel_name='Public', channel_secret=b'')

            async def disconnect(self):
                await super().disconnect()
                self.is_connected = False

        async def connect(config):
            self.assertTrue(all(device.closed for device in devices))
            device = Device()
            device.endpoint = (config.transport, config.port, config.host)
            devices.append(device)
            return device

        def wait_for(predicate):
            deadline = time.monotonic() + 4
            while not predicate() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(predicate())

        with tempfile.TemporaryDirectory() as tmp, patch.dict('sys.modules', {'meshcore': SimpleNamespace(EventType=events)}):
            store = Store(Path(tmp) / 'radio.db')
            store.save_list('Reply', ['pong'])
            store.set_settings(bot_enabled=1, bot_channel=5, bot_trigger='ping', bot_page_name='Reply')
            stop = threading.Event()
            with SharedRadio(store, connector=connect) as owner:
                bot = BotListener(store, stop)
                bot.start()
                try:
                    wait_for(lambda: devices and devices[0].started.is_set())
                    device = devices[0]
                    radio = Radio.from_store(store)
                    session = PublisherSession(radio, key, 'password')
                    second_room = PublisherSession(radio, other, 'password')
                    self.assertEqual(radio.snapshot()[1][0]['name'], 'Public')
                    store.save({'name':'Scheduled', 'channels':[2], 'messages':['scheduled'], 'interval':1800, 'enabled':True})
                    with store.connect() as db:
                        db.execute('UPDATE schedules SET next_send=0')
                    scheduler = Scheduler(store, stop)
                    scheduler.start()

                    async def deliver():
                        device.responses.append('Node: ping')
                        device.notify()

                    owner.run(deliver)
                    session.send('one')
                    second_room.send('two')
                    wait_for(lambda: (5, 'pong') in device.sent and (2, 'scheduled') in device.sent)
                    self.assertEqual(device.room_posts, [(key, 'one'), (other, 'two')])
                    self.assertEqual(len(devices), 1)
                    replacement = PublisherSession(radio, key, 'password')
                    session.close()  # Retiring the old browser must not retire its replacement.
                    with self.assertRaises(PublisherSessionExpired):
                        session.send('stale')
                    replacement.send('three')
                    store.set_settings(connection_type='tcp', tcp_host='configured.test')
                    wait_for(lambda: len(devices) == 2)  # Idle maintenance, no new call needed.
                    self.assertEqual(devices[1].endpoint[2], 'configured.test')
                    with self.assertRaises(PublisherSessionExpired):
                        second_room.send('never sent on the replacement connection')
                    self.assertEqual(devices[1].room_posts, [])
                finally:
                    stop.set()
                    bot.join(4)
                    if 'scheduler' in locals():
                        scheduler.join(4)
