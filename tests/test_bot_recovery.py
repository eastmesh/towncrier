import asyncio
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch
from urllib.request import urlopen

from towncrier.app import Handler
from towncrier.db import Store
from towncrier.engine import BotListener


EVENTS = SimpleNamespace(**{name: name for name in (
    'NO_MORE_MSGS', 'ERROR', 'MESSAGES_WAITING', 'CHANNEL_MSG_RECV', 'OK')})


def result(kind, **payload):
    return SimpleNamespace(type=kind, payload=payload)


class Companion:
    is_connected = True

    def __init__(self):
        self.callbacks = {}
        self.commands = self
        self.responses = []
        self.sent = []
        self.first_send = asyncio.Event()
        self.live_fetch = asyncio.Event()
        self.started = asyncio.Event()
        self.closed = False
        self.send_result = result(EVENTS.OK)

    def subscribe(self, kind, callback):
        self.callbacks[kind] = callback
        return kind

    def unsubscribe(self, kind):
        del self.callbacks[kind]

    async def stop_auto_message_fetching(self):
        pass

    async def disconnect(self):
        self.closed = True

    def notify(self):
        self.callbacks[EVENTS.MESSAGES_WAITING](None)

    async def get_msg(self):
        if not self.started.is_set():
            self.started.set()
            return result(EVENTS.NO_MORE_MSGS)
        response = self.responses.pop(0)
        if isinstance(response, str):
            self.callbacks[EVENTS.CHANNEL_MSG_RECV](result(
                EVENTS.CHANNEL_MSG_RECV, text=response, channel_idx=5, sender_timestamp=len(response)))
            self.live_fetch.set()
            return result(EVENTS.NO_MORE_MSGS)
        return response

    async def send_chan_msg(self, channel, text):
        self.sent.append((channel, text))
        self.first_send.set()
        return self.send_result


class BotRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'bot.db'
        self.store = Store(self.path)
        self.store.save_list('Reply', ['one', 'two'])
        for key, value in {'enabled': 1, 'channel': 5, 'trigger': 'ping',
                           'page_name': 'Reply', 'message_delay': 3600}.items():
            self.store.set_setting('bot_' + key, value)
        self.stop = threading.Event()
        self.bot = BotListener(self.store, self.stop)
        self.mc = Companion()

        async def connect():
            return self.mc

        self.enterContext(patch.dict(sys.modules, {'meshcore': SimpleNamespace(EventType=EVENTS)}))
        self.enterContext(patch('towncrier.engine.Radio.from_store',
                                return_value=SimpleNamespace(_connect=connect)))

    async def start_session(self):
        task = asyncio.create_task(self.bot._run_async())

        async def cleanup():
            self.stop.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        self.addAsyncCleanup(cleanup)
        await asyncio.wait_for(self.mc.started.wait(), 1)
        return task

    async def test_fetch_and_record_during_long_reply_delay(self):
        self.mc.responses = ['Node: ping', 'Node: normal traffic']
        task = await self.start_session()
        self.mc.notify()
        await asyncio.wait_for(self.mc.first_send.wait(), 1)
        self.mc.live_fetch.clear()
        self.mc.notify()
        try:
            await asyncio.wait_for(self.mc.live_fetch.wait(), 1)
            self.assertEqual(self.store.bot_messages(5)[0]['text'], 'Node: normal traffic')
        finally:
            self.stop.set()
            await asyncio.wait_for(task, 1)
        self.assertEqual(self.mc.sent, [(5, 'one')])
        self.assertTrue(self.mc.closed)
        history = Store(self.path).bot_messages(5)
        self.assertEqual(history[1]['reply_status'], 'interrupted')
        self.assertEqual(history[0]['reply_status'], '')

    async def test_ambiguous_fetch_retries_without_another_notification(self):
        self.mc.responses = [result(EVENTS.ERROR, reason='no_event_received'), 'Node: recovered']
        await self.start_session()
        self.mc.notify()
        await asyncio.wait_for(self.mc.live_fetch.wait(), 2)
        self.assertEqual(self.store.bot_messages(5)[0]['text'], 'Node: recovered')

    async def test_repeated_ambiguous_fetch_requests_reconnect(self):
        self.mc.responses = [result(EVENTS.ERROR, reason='timeout')] * 3
        task = await self.start_session()
        self.mc.notify()
        with self.assertRaisesRegex(ConnectionError, 'fetch'):
            await asyncio.wait_for(task, 3)
        self.assertTrue(self.mc.closed)

        # A reconnect must recover queued traffic even without another notification.
        self.mc = Companion()
        self.mc.started.set()
        self.mc.responses = ['Node: ping']
        self.store.set_setting('bot_message_delay', 0)
        recovered = await self.start_session()
        await asyncio.wait_for(self.mc.first_send.wait(), 1)
        self.stop.set()
        await asyncio.wait_for(recovered, 1)
        self.assertEqual(self.mc.sent, [(5, 'one'), (5, 'two')])
        self.assertEqual(Store(self.path).bot_messages(5)[0]['reply_status'], 'sent')

    async def test_reply_result_is_saved_on_the_trigger_row(self):
        self.store.set_setting('bot_message_delay', 0)
        self.mc.send_result = result(EVENTS.ERROR, reason='no_event_received')
        self.mc.responses = ['Node: ping']
        task = await self.start_session()
        self.mc.notify()
        await asyncio.wait_for(self.mc.first_send.wait(), 1)
        self.stop.set()
        await asyncio.wait_for(task, 1)
        row, = Store(self.path).bot_messages(5)
        self.assertEqual(row['reply_status'], 'unconfirmed')
        self.assertIn('confirmation', row['error'])
        self.assertEqual(self.mc.sent, [(5, 'one'), (5, 'two')])

    async def test_missing_page_fails_only_its_trigger_row(self):
        self.store.set_setting('bot_page_name', 'Missing')
        self.mc.responses = ['Node: ping', 'Node: ordinary']
        task = await self.start_session()
        self.mc.notify()
        await asyncio.wait_for(self.mc.live_fetch.wait(), 1)
        self.mc.live_fetch.clear()
        self.mc.notify()
        await asyncio.wait_for(self.mc.live_fetch.wait(), 1)
        self.stop.set()
        await asyncio.wait_for(task, 1)
        ordinary, trigger = Store(self.path).bot_messages(5)
        self.assertEqual(ordinary['reply_status'], '')
        self.assertEqual(trigger['reply_status'], 'failed')
        self.assertIn('missing', trigger['error'])
        self.assertFalse(self.mc.sent)


class BotHistoryTests(unittest.TestCase):
    def test_legacy_outcomes_stay_unknown_and_pending_replies_are_not_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'legacy.db'
            db = sqlite3.connect(path)
            try:
                with db:
                    db.execute("""CREATE TABLE bot_messages(id INTEGER PRIMARY KEY,
                        received_at REAL NOT NULL, channel INTEGER NOT NULL, text TEXT NOT NULL,
                        triggered INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '')""")
                    db.execute("INSERT INTO bot_messages VALUES (1,0,5,'old ping',1,'')")
            finally:
                db.close()
            store = Store(path)
            store.record_bot_message(5, 'new ping', True)
            reopened = Store(path)
            reopened.interrupt_pending_bot_replies()
            new, old = reopened.bot_messages(5)
            self.assertEqual(new['reply_status'], 'interrupted')
            self.assertEqual(old['reply_status'], '')

    def test_bot_history_endpoint_and_page_expose_reply_outcomes(self):
        with tempfile.TemporaryDirectory() as tmp:
            history_store = Store(Path(tmp) / 'history.db')
            row_id = history_store.record_bot_message(5, 'Node: ping', True)
            history_store.finish_bot_reply(row_id, 'failed', '<failure detail>')

            class TestHandler(Handler):
                store = history_store
            with ThreadingHTTPServer(('127.0.0.1', 0), TestHandler) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    base = f'http://127.0.0.1:{server.server_port}'
                    with urlopen(base + '/publisher-api/bot-messages?channel=5') as response:
                        row, = json.load(response)['messages']
                    with urlopen(base + '/bot') as response:
                        page = response.read().decode()
                finally:
                    server.shutdown()
                    thread.join()
            self.assertEqual(row['reply_status'], 'failed')
            self.assertEqual(row['error'], '<failure detail>')
            self.assertIn('Reply failed', page)
            self.assertIn('detail.textContent=item.error', page)
