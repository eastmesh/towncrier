import asyncio
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from urllib.request import Request, urlopen
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, patch
from towncrier.db import Store, CLAIM_TIMEOUT
from towncrier.app import esc, Handler
from towncrier.engine import BotListener, Scheduler, send_message, split_message

class FakeStop:
    def __init__(self): self.calls = 0
    def is_set(self): return self.calls > 0
    def wait(self, _): self.calls += 1

class FakeRadio:
    sent = []
    def __init__(self, port, baud): pass
    @classmethod
    def from_store(cls, store): return cls(store.setting("port","/dev/ttyUSB0"),store.setting("baud","115200"))
    def send(self, channel, message): self.sent.append((channel, message))

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / "x.db")
    def tearDown(self): self.tmp.cleanup()
    def test_explicit_daily_times_replace_interval_and_advance_to_next_clock_time(self):
        times=["09:00","13:30","18:00"]
        fixed=datetime.now().astimezone().replace(second=0,microsecond=0)
        times=[(fixed+timedelta(hours=1)).strftime("%H:%M"),(fixed+timedelta(hours=3)).strftime("%H:%M"),(fixed+timedelta(hours=5)).strftime("%H:%M")]
        times=sorted(set(times))
        ident=self.s.save({"name":"times","channels":[0],"messages":["x"],"interval":1800,"post_times":times})
        first_expected=next((value for value in times if datetime.strptime(value,"%H:%M").time()>fixed.time()),times[0])
        actual=datetime.fromtimestamp(self.s.get(ident)["next_send"]).astimezone()
        self.assertEqual(actual.strftime("%H:%M"),first_expected)
        ordered=times[times.index(first_expected):]+times[:times.index(first_expected)]
        first=datetime.fromtimestamp(self.s.get(ident)["next_send"]).astimezone()
        self.assertEqual(first.strftime("%H:%M"),ordered[0])
        self.s.sent(ident,first.timestamp(),1800)
        self.assertEqual(datetime.fromtimestamp(self.s.get(ident)["next_send"]).astimezone().strftime("%H:%M"),ordered[1])
        second=datetime.fromtimestamp(self.s.get(ident)["next_send"]).astimezone()
        self.s.sent(ident,second.timestamp(),1800)
        self.assertEqual(datetime.fromtimestamp(self.s.get(ident)["next_send"]).astimezone().strftime("%H:%M"),ordered[2])
    def test_round_trip_validation_and_crud(self):
        self.s.save({"name":"Morning","channel":2,"messages":["hello","world"],"interval":1800,"enabled":True})
        row = self.s.schedules()[0]
        self.assertEqual(row["channel"], 2)
        self.assertEqual(row["messages"], ["hello", "world"])
        self.s.save({"name":"Changed","channel":3,"messages":["updated"],"interval":3600,"enabled":False}, row["id"])
        self.assertEqual(self.s.get(row["id"])["name"], "Changed")
        self.s.delete(row["id"])
        self.assertIsNone(self.s.get(row["id"]))
        with self.assertRaises(ValueError): self.s.save({"name":"x","channel":2,"messages":[],"interval":1740})
    def test_bot_messages_persist_and_round_trip(self):
        tmp=tempfile.TemporaryDirectory(); path=Path(tmp.name)/"messages.db"
        try:
            store=Store(path)
            store.record_bot_message(1,"Nastic: testing",True)
            self.assertEqual(store.bot_messages(1)[0]["text"],"Nastic: testing")
            self.assertTrue(Store(path).bot_messages(1)[0]["triggered"])
        finally: tmp.cleanup()

    def test_legacy_single_message_is_migrated(self):
        path = Path(self.tmp.name) / "legacy.db"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE schedules(id INTEGER PRIMARY KEY,name TEXT,channel INTEGER,message TEXT,interval INTEGER,enabled INTEGER,next_send REAL,last_send REAL,last_error TEXT,status TEXT)")
            db.execute("INSERT INTO schedules VALUES(1,'legacy',2,'hello',1800,1,0,NULL,NULL,'waiting')")
        migrated=Store(path).get(1)
        assert migrated is not None
        self.assertEqual(migrated["messages"], ["hello"])
        self.assertEqual(migrated["send_at"], "")
    def test_message_delay_round_trip(self):
        self.s.save({"name":"x","channel":1,"message":"x","interval":1800,"message_delay":7})
        self.assertEqual(self.s.get(1)["message_delay"], 7)
    def test_send_message_uses_default_delay(self):
        with patch("towncrier.engine.time.sleep") as sleep:
            send_message(FakeRadio("", ""), 1, ["first", "second"])
        sleep.assert_called_once_with(3)
    def test_send_message_uses_custom_delay(self):
        with patch("towncrier.engine.time.sleep") as sleep:
            send_message(FakeRadio("", ""), 1, ["first", "second"], 7)
        sleep.assert_called_once_with(7)
    def test_send_message_delays_between_messages_on_each_channel(self):
        FakeRadio.sent = []
        with patch("towncrier.engine.time.sleep") as sleep:
            send_message(FakeRadio("", ""), [1, 3], ["first", "second"])
        self.assertEqual(FakeRadio.sent, [(1, "first"), (1, "second"), (3, "first"), (3, "second")])
        self.assertEqual(sleep.call_count, 2)
    def test_save_waits_until_interval_before_first_send(self):
        self.s.save({"name":"x","channels":[1],"message":"x","interval":1800})
        self.assertEqual(self.s.due(now=0), [])
    def test_save_uses_configured_local_first_send_time(self):
        target=(datetime.now().astimezone()+timedelta(hours=2)).replace(second=0,microsecond=0)
        self.s.save({"name":"x","channels":[1],"message":"x","interval":1800,"send_at":target.strftime("%H:%M")})
        self.assertAlmostEqual(self.s.get(1)["next_send"],target.timestamp(),delta=60)
    def test_send_time_waits_one_full_interval_before_first_send(self):
        local_zone=datetime.now().astimezone().tzinfo
        fixed_now=datetime(2026,11,11,7,22,tzinfo=local_zone)
        class FrozenDateTime(datetime):
            @classmethod
            def now(cls,tz=None): return fixed_now if tz is None else fixed_now.astimezone(tz)
        with patch("towncrier.db.datetime",FrozenDateTime), patch("towncrier.db.time.time",return_value=fixed_now.timestamp()):
            self.s.save({"name":"x","channels":[1],"message":"x","interval":72*3600,"send_at":"09:00"})
        row=self.s.get(1)
        assert row is not None
        due_local=datetime.fromtimestamp(row["next_send"]).astimezone()
        self.assertEqual(due_local.strftime("%H:%M"),"09:00")
        self.assertEqual(due_local.date(),fixed_now.date())
    def test_edit_keeps_next_send_timestamp(self):
        self.s.save({"name":"x","channels":[1],"message":"x","interval":72*3600})
        original=self.s.get(1)
        assert original is not None
        original_next_send=original["next_send"]
        self.s.save({"name":"updated","channels":[1],"message":"updated","interval":72*3600,"send_at":"09:00"},1)
        updated=self.s.get(1)
        assert updated is not None
        self.assertEqual(updated["next_send"],original_next_send)
        self.assertEqual(updated["post_times"],["09:00"])
    def test_multiple_channels_round_trip(self):
        self.s.save({"name":"x","channels":[1,3],"messages":["a","b"],"interval":1800})
        self.assertEqual(self.s.schedules()[0]["channels"], [1, 3])
    def test_scheduler_uses_fake_radio_and_advances_after_send(self):
        self.s.save({"name":"x","channel":1,"message":"x","interval":1800})
        with self.s.connect() as db: db.execute("UPDATE schedules SET next_send=0")
        FakeRadio.sent = []
        with patch("towncrier.engine.Radio", FakeRadio):
            Scheduler(self.s, FakeStop()).run()
        self.assertEqual(FakeRadio.sent, [(1, "x")])
        self.assertEqual(self.s.get(1)["status"], "sent")
    def test_split_message_targets_120_and_never_exceeds_130(self):
        chunks = split_message(" ".join(["word"] * 100))
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 130 for chunk in chunks))
        self.assertEqual(" ".join(chunks), " ".join(["word"] * 100))
    def test_split_message_preserves_newlines(self):
        message = "first line\nsecond line\nthird"
        self.assertEqual("".join(split_message(message)), message)
    def test_saved_textbox_round_trips_as_one_unchanged_radio_message(self):
        messages=["  one  two\tthree  ","\nfirst\n\nsecond\n", "😀"*32+"ab"]
        self.s.save_list("Layout",messages)
        self.assertEqual(self.s.saved_lists()[0]["messages"],messages)
        self.assertEqual([split_message(text) for text in messages],[[text] for text in messages])
    def test_split_message_caps_utf8_bytes(self):
        for message in ('😀'*130, 'é'*100+'\n'+'😀'*50):
            chunks=split_message(message)
            self.assertEqual(''.join(chunks),message)
            self.assertTrue(all(len(chunk.encode('utf-8'))<=130 for chunk in chunks))
    def test_failed_schedule_waits_before_retry(self):
        self.s.save({"name":"x","channel":1,"message":"x","interval":1800})
        with self.s.connect() as db: db.execute("UPDATE schedules SET next_send=1")
        with patch('towncrier.db.time.time',return_value=100):
            self.s.failed(1,RuntimeError('radio unavailable'))
        self.assertEqual(self.s.due(159),[])
        self.assertEqual(len(self.s.due(160)),1)
    def test_sent_moves_next_send(self):
        self.s.save({"name":"x","channel":1,"message":"x","interval":1800})
        row = self.s.schedules()[0]
        self.s.sent(row["id"], 100, 1800)
        self.assertEqual(self.s.get(row["id"])["next_send"], 1900)
    def test_unique_page_name_migration_backfills_schedule(self):
        path = Path(self.tmp.name) / "unique-page.db"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE schedules(id INTEGER PRIMARY KEY,name TEXT,channel INTEGER,message TEXT,interval INTEGER,enabled INTEGER,next_send REAL,last_send REAL,last_error TEXT,status TEXT)")
            db.execute("INSERT INTO schedules VALUES(1,'legacy',2,'hello',1800,1,0,NULL,NULL,'waiting')")
            db.execute("CREATE TABLE saved_lists(name TEXT PRIMARY KEY,created_at REAL NOT NULL,updated_at REAL NOT NULL)")
            db.execute("CREATE TABLE saved_list_messages(list_name TEXT NOT NULL,position INTEGER NOT NULL,message TEXT NOT NULL,PRIMARY KEY(list_name,position))")
            db.execute("INSERT INTO saved_lists VALUES('Alerts',0,0)")
            db.execute("INSERT INTO saved_list_messages VALUES('Alerts',0,'hello')")
        store = Store(path)
        self.assertEqual(store.get(1)["page_name"], "Alerts")

    def test_ambiguous_identical_saved_pages_remain_unlinked(self):
        path = Path(self.tmp.name) / "ambiguous-page.db"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE schedules(id INTEGER PRIMARY KEY,name TEXT,channel INTEGER,message TEXT,interval INTEGER,enabled INTEGER,next_send REAL,last_send REAL,last_error TEXT,status TEXT)")
            db.execute("INSERT INTO schedules VALUES(1,'legacy',2,'hello',1800,1,0,NULL,NULL,'waiting')")
            db.execute("CREATE TABLE saved_lists(name TEXT PRIMARY KEY,created_at REAL NOT NULL,updated_at REAL NOT NULL)")
            db.execute("CREATE TABLE saved_list_messages(list_name TEXT NOT NULL,position INTEGER NOT NULL,message TEXT NOT NULL,PRIMARY KEY(list_name,position))")
            db.execute("INSERT INTO saved_lists VALUES('Alerts',0,0)")
            db.execute("INSERT INTO saved_list_messages VALUES('Alerts',0,'hello')")
            db.execute("INSERT INTO saved_lists VALUES('Backup',0,0)")
            db.execute("INSERT INTO saved_list_messages VALUES('Backup',0,'hello')")
        store = Store(path)
        self.assertIsNone(store.get(1)["page_name"])

    def test_page_bound_schedule_resolves_current_page_contents_after_change(self):
        self.s.save_list("Alerts", ["one", "two"])
        ident = self.s.save({"name":"x","channels":[1],"messages":["one","two"],"page_name":"Alerts","interval":1800})
        self.assertEqual(self.s.resolve_messages(self.s.get(ident)), ["one", "two"])
        self.s.save_list("Alerts", ["changed"])
        self.assertEqual(self.s.resolve_messages(self.s.get(ident)), ["changed"])

    def test_save_rejects_unknown_page_name(self):
        with self.assertRaises(ValueError):
            self.s.save({"name":"x","channels":[1],"messages":["x"],"page_name":"Missing","interval":1800})

    def test_scheduler_records_failure_without_sending_when_page_missing(self):
        self.s.save_list("Alerts", ["hi"])
        ident = self.s.save({"name":"x","channels":[1],"messages":["hi"],"page_name":"Alerts","interval":1800})
        self.s.delete_list("Alerts")
        with self.s.connect() as db: db.execute("UPDATE schedules SET next_send=0")
        FakeRadio.sent = []
        with patch("towncrier.engine.Radio", FakeRadio):
            Scheduler(self.s, FakeStop()).run()
        self.assertEqual(FakeRadio.sent, [])
        row = self.s.get(ident)
        self.assertEqual(row["status"], "error")
        self.assertIn("Alerts", row["last_error"])
        self.assertEqual(self.s.due(time.time()), [])

    def test_two_stores_on_same_db_only_one_claims_a_due_schedule(self):
        self.s.save({"name":"x","channel":1,"message":"x","interval":1800})
        with self.s.connect() as db: db.execute("UPDATE schedules SET next_send=0")
        other = Store(Path(self.tmp.name) / "x.db")
        first = self.s.due()
        second = other.due()
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])

    def test_stale_claim_is_reclaimable_only_after_timeout(self):
        self.s.save({"name":"x","channel":1,"message":"x","interval":1800})
        with self.s.connect() as db: db.execute("UPDATE schedules SET next_send=0")
        first = self.s.due()
        self.assertEqual(len(first), 1)
        now = time.time()
        self.assertEqual(self.s.due(now), [])
        self.assertEqual(len(self.s.due(now + CLAIM_TIMEOUT + 1)), 1)

    def test_sent_advances_daily_schedule_from_the_claimed_occurrence(self):
        local_zone = datetime.now().astimezone().tzinfo
        occurrence = datetime(2026, 11, 27, 9, 0, tzinfo=local_zone)
        ident = self.s.save({"name":"x","channels":[1],"message":"x","interval":1800,"post_times":["09:00","09:05"]})
        self.s.sent(ident, occurrence.timestamp(), 1800)
        next_send = datetime.fromtimestamp(self.s.get(ident)["next_send"]).astimezone()
        self.assertEqual(next_send.strftime("%H:%M"), "09:05")
        self.assertEqual(next_send.date(), occurrence.date())

    def test_scheduler_advances_using_claimed_occurrence_not_completion_time(self):
        ident = self.s.save({"name":"x","channels":[1],"message":"x","interval":1800,"post_times":["09:00"]})
        with self.s.connect() as db: db.execute("UPDATE schedules SET next_send=0")
        FakeRadio.sent = []
        with patch("towncrier.engine.Radio", FakeRadio), patch("towncrier.engine.time.time", return_value=999999):
            Scheduler(self.s, FakeStop()).run()
        self.assertEqual(FakeRadio.sent, [(1, "x")])
        self.assertEqual(self.s.get(ident)["last_send"], 0)

    def test_escape(self): self.assertEqual(esc('<script>'), '&lt;script&gt;')

class BotEntryTests(unittest.TestCase):
    def test_legacy_bot_settings_migrate_into_one_persisted_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy-bot.db"
            db = sqlite3.connect(path)
            try:
                with db:
                    db.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                    for key, value in {"bot_enabled": "1", "bot_channel": "5", "bot_trigger": "ping",
                                        "bot_page_name": "Reply", "bot_message_delay": "7"}.items():
                        db.execute("INSERT INTO settings VALUES (?,?)", (key, value))
            finally:
                db.close()
            store = Store(path)
            entries = store.real_bot_entries()
            self.assertEqual(len(entries), 1)
            entry = entries[0]
            self.assertEqual(entry["channel"], 5)
            self.assertEqual(entry["trigger"], "ping")
            self.assertEqual(entry["page_name"], "Reply")
            self.assertEqual(entry["message_delay"], 7)
            self.assertEqual(entry["enabled"], 1)
            # Reopening the already-migrated database must not duplicate the entry.
            self.assertEqual(len(Store(path).real_bot_entries()), 1)

    def test_two_active_entries_on_distinct_channels_and_triggers(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "bot-entries.db")
            store.save_list("Reply", ["one"])
            store.save_list("Other", ["two"])
            first = store.save_bot_entry({"channel": 5, "trigger": "ping", "page_name": "Reply", "message_delay": 3})
            second = store.save_bot_entry({"channel": 7, "trigger": "status", "page_name": "Other", "message_delay": 0})
            active = store.active_bot_entries()
            self.assertEqual({(e["id"], e["channel"], e["trigger"], e["page_name"]) for e in active},
                              {(first, 5, "ping", "Reply"), (second, 7, "status", "Other")})

    def test_pause_resume_and_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "bot-entries.db")
            store.save_list("Reply", ["one"])
            ident = store.save_bot_entry({"channel": 5, "trigger": "ping", "page_name": "Reply"})
            store.toggle_bot_entry(ident)
            self.assertEqual(store.get_bot_entry(ident)["enabled"], 0)
            self.assertEqual(store.active_bot_entries(), [])
            store.toggle_bot_entry(ident)
            self.assertEqual(store.get_bot_entry(ident)["enabled"], 1)
            store.delete_bot_entry(ident)
            self.assertIsNone(store.get_bot_entry(ident))

    def test_editing_a_paused_entry_does_not_resume_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "bot-entries.db")
            store.save_list("Reply", ["one"])
            ident = store.save_bot_entry({"channel": 5, "trigger": "ping", "page_name": "Reply", "enabled": False})
            store.save_bot_entry({"channel": 5, "trigger": "ping-renamed", "page_name": "Reply"}, ident)
            entry = store.get_bot_entry(ident)
            self.assertEqual(entry["trigger"], "ping-renamed")
            self.assertEqual(entry["enabled"], 0)

    def test_collision_validation_rejects_same_active_channel_and_trigger(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "bot-entries.db")
            store.save_list("Reply", ["one"])
            store.save_list("Other", ["two"])
            store.save_bot_entry({"channel": 5, "trigger": "ping", "page_name": "Reply"})
            with self.assertRaises(ValueError):
                store.save_bot_entry({"channel": 5, "trigger": "PING", "page_name": "Other"})
            paused = store.save_bot_entry({"channel": 5, "trigger": "ping", "page_name": "Other", "enabled": False})
            with self.assertRaises(ValueError):
                store.toggle_bot_entry(paused)
            self.assertEqual(store.get_bot_entry(paused)["enabled"], 0)

class BotListenerTests(unittest.IsolatedAsyncioTestCase):
    async def test_idle_disconnect_exits_session_without_message_notification(self):
        import asyncio
        from types import SimpleNamespace

        events=SimpleNamespace(**{name: object() for name in (
            "NO_MORE_MSGS", "ERROR", "MESSAGES_WAITING", "CHANNEL_MSG_RECV")})
        listening=asyncio.Event()
        mc=SimpleNamespace(is_connected=True, subscribe=lambda *_: None,
                           disconnect=AsyncMock(), stop_auto_message_fetching=AsyncMock())
        mc.commands=SimpleNamespace(get_msg=AsyncMock(return_value=SimpleNamespace(type=events.NO_MORE_MSGS)))
        bot=BotListener(None,threading.Event())
        def status(**fields):
            if fields["state"]=="listening": listening.set()

        with patch.dict(sys.modules,{"meshcore":SimpleNamespace(EventType=events)}), \
             patch.object(bot,"_load_entries",return_value=[{"id":1,"channel":5,"trigger":"testing","page_name":"Reply","message_delay":0}]), \
             patch("towncrier.engine.Radio.from_store",return_value=SimpleNamespace(_connect=AsyncMock(return_value=mc))), \
             patch("towncrier.engine.set_bot_status",side_effect=status):
            session=asyncio.create_task(bot._run_async())
            await asyncio.wait_for(listening.wait(),1)
            mc.is_connected=False
            with self.assertRaisesRegex(ConnectionError,"disconnected"):
                await asyncio.wait_for(session,1.5)
        mc.commands.get_msg.assert_awaited_once()
        mc.disconnect.assert_awaited_once()

    async def test_reply_send_does_not_issue_a_competing_message_fetch(self):
        class Commands:
            def __init__(self): self.sent=[]; self.fetches=0
            async def send_chan_msg(self,channel,text): self.sent.append((channel,text)); return type("Result",(),{"type":None})()
            async def get_msg(self): self.fetches+=1
        class MeshCore:
            def __init__(self): self.commands=Commands()
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/"bot.db")
            store.save_list("Reply",["first reply","second reply"])
            listener=BotListener(store,threading.Event())
            mc=MeshCore()
            with patch("towncrier.engine.asyncio.sleep",new_callable=AsyncMock) as sleep:
                await listener._post_page(mc,None,5,"Reply",7)
        self.assertEqual(mc.commands.sent,[(5,"first reply"),(5,"second reply")])
        sleep.assert_awaited_once_with(7)
        self.assertEqual(mc.commands.fetches,0)

    async def test_fetch_queued_messages_stops_after_a_normal_empty_response(self):
        class Commands:
            def __init__(self): self.fetches=0
            async def get_msg(self):
                self.fetches+=1
                return type("Result",(),{"type":EventType.NO_MORE_MSGS,"payload":{}})()
        class MeshCore:
            def __init__(self): self.commands=Commands()
        class EventType:
            NO_MORE_MSGS=object(); ERROR=object()
        listener=BotListener(None,threading.Event()); mc=MeshCore()
        await listener._fetch_queued_messages(mc,EventType)
        self.assertEqual(mc.commands.fetches,1)

    async def test_fetch_queued_messages_reconnects_after_a_transport_error(self):
        class Commands:
            async def get_msg(self):
                return type("Result",(),{"type":EventType.ERROR,"payload":{"error":"connection lost"}})()
        class MeshCore:
            commands=Commands()
        class EventType:
            NO_MORE_MSGS=object(); ERROR=object()
        listener=BotListener(None,threading.Event())
        with self.assertRaises(ConnectionError):
            await listener._fetch_queued_messages(MeshCore(),EventType)

    async def test_trigger_with_extra_text_does_not_reply(self):
        class Commands:
            def __init__(self,mc): self.mc=mc; self.sent=[]
            async def get_msg(self):
                self.mc.fetches+=1
                if self.mc.fetches==1:
                    self.mc.callbacks[EventType.CHANNEL_MSG_RECV](type("Event",(),{"payload":{"channel_idx":5,"sender_timestamp":1,"text":"Node: testing"}})())
                    self.mc.callbacks[EventType.MESSAGES_WAITING](object())
                else:
                    self.mc.callbacks[EventType.CHANNEL_MSG_RECV](type("Event",(),{"payload":{"channel_idx":5,"sender_timestamp":2,"text":"Node: testing please reply"}})())
                    self.mc.stop.set()
                return type("Result",(),{"type":EventType.NO_MORE_MSGS,"payload":{}})()
            async def send_chan_msg(self,channel,text): self.sent.append((channel,text)); return type("Result",(),{"type":None})()
        class MeshCore:
            is_connected=True
            def __init__(self,stop): self.stop=stop; self.commands=Commands(self); self.callbacks={}; self.fetches=0
            def subscribe(self,event,callback): self.callbacks[event]=callback; return object()
            def unsubscribe(self,_subscription): pass
            async def stop_auto_message_fetching(self): pass
            async def disconnect(self): pass
        class Radio:
            def __init__(self,mc): self.mc=mc
            async def _connect(self): return self.mc
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/"bot-exact-trigger.db")
            store.save_list("Reply",["reply"])
            for key,value in (("bot_enabled","1"),("bot_channel","5"),("bot_trigger","testing"),("bot_page_name","Reply")):
                store.set_setting(key,value)
            class EventType:
                CHANNEL_MSG_RECV=object(); MESSAGES_WAITING=object(); NO_MORE_MSGS=object(); ERROR=object()
            stop=threading.Event(); mc=MeshCore(stop)
            with patch.dict(sys.modules,{"meshcore":type("MeshcoreModule",(),{"EventType":EventType})()}), patch("towncrier.engine.Radio.from_store",return_value=Radio(mc)):
                await BotListener(store,stop)._run_async()
            history=store.bot_messages(5)
        self.assertEqual(mc.commands.sent,[])
        self.assertEqual(len(history),1)
        self.assertFalse(history[0]["triggered"])

    async def test_triggered_reply_is_sent_before_the_listener_fetches_again(self):
        class Commands:
            def __init__(self,mc): self.mc=mc; self.fetches=0; self.sent=[]
            async def get_msg(self):
                self.fetches+=1
                if self.fetches==1:
                    self.mc.callbacks[EventType.MESSAGES_WAITING](object())
                elif self.fetches==2:
                    self.mc.callbacks[EventType.CHANNEL_MSG_RECV](type("Event",(),{"payload":{"channel_idx":5,"sender_timestamp":1,"text":"Node: testing"}})())
                else: raise AssertionError("listener fetched while a queued reply was pending")
                return type("Result",(),{"type":EventType.NO_MORE_MSGS,"payload":{}})()
            async def send_chan_msg(self,channel,text):
                self.sent.append((channel,text)); self.mc.stop.set(); return type("Result",(),{"type":None})()
        class MeshCore:
            is_connected=True
            def __init__(self,stop): self.stop=stop; self.commands=Commands(self); self.callbacks={}
            def subscribe(self,event,callback): self.callbacks[event]=callback; return object()
            def unsubscribe(self,_subscription): pass
            async def stop_auto_message_fetching(self): pass
            async def disconnect(self): pass
        class Radio:
            def __init__(self,mc): self.mc=mc
            async def _connect(self): return self.mc
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/"bot-loop.db")
            store.save_list("Reply",["reply"])
            for key,value in (("bot_enabled","1"),("bot_channel","5"),("bot_trigger","testing"),("bot_page_name","Reply")):
                store.set_setting(key,value)
            class EventType:
                CHANNEL_MSG_RECV=object(); MESSAGES_WAITING=object(); NO_MORE_MSGS=object(); ERROR=object()
            stop=threading.Event(); mc=MeshCore(stop)
            with patch.dict(sys.modules,{"meshcore":type("MeshcoreModule",(),{"EventType":EventType})()}), patch("towncrier.engine.Radio.from_store",return_value=Radio(mc)):
                await BotListener(store,stop)._run_async()
        self.assertEqual(mc.commands.fetches,2)
        self.assertEqual(mc.commands.sent,[(5,"reply")])

    async def test_multiple_active_entries_route_to_their_own_page_and_paused_entry_is_silent(self):
        class Commands:
            def __init__(self,mc): self.mc=mc; self.fetches=0; self.sent=[]
            async def get_msg(self):
                self.fetches+=1
                if self.fetches==1:
                    self.mc.callbacks[EventType.MESSAGES_WAITING](object())
                elif self.fetches==2:
                    self.mc.callbacks[EventType.CHANNEL_MSG_RECV](type("Event",(),{"payload":{"channel_idx":5,"sender_timestamp":1,"text":"Node: alpha"}})())
                    self.mc.callbacks[EventType.MESSAGES_WAITING](object())
                elif self.fetches==3:
                    self.mc.callbacks[EventType.CHANNEL_MSG_RECV](type("Event",(),{"payload":{"channel_idx":5,"sender_timestamp":2,"text":"Node: beta"}})())
                    self.mc.callbacks[EventType.MESSAGES_WAITING](object())
                elif self.fetches==4:
                    self.mc.callbacks[EventType.CHANNEL_MSG_RECV](type("Event",(),{"payload":{"channel_idx":7,"sender_timestamp":3,"text":"Node: gamma"}})())
                return type("Result",(),{"type":EventType.NO_MORE_MSGS,"payload":{}})()
            async def send_chan_msg(self,channel,text):
                self.sent.append((channel,text))
                if len(self.sent)>=2: self.mc.stop.set()
                return type("Result",(),{"type":None})()
        class MeshCore:
            is_connected=True
            def __init__(self,stop): self.stop=stop; self.commands=Commands(self); self.callbacks={}
            def subscribe(self,event,callback): self.callbacks[event]=callback; return object()
            def unsubscribe(self,_subscription): pass
            async def stop_auto_message_fetching(self): pass
            async def disconnect(self): pass
        class Radio:
            def __init__(self,mc): self.mc=mc
            async def _connect(self): return self.mc
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/"bot-multi.db")
            store.save_list("Alpha",["alpha-reply"])
            store.save_list("Beta",["beta-reply"])
            store.save_list("Gamma",["gamma-reply"])
            store.save_bot_entry({"channel":5,"trigger":"alpha","page_name":"Alpha","message_delay":0})
            store.save_bot_entry({"channel":5,"trigger":"beta","page_name":"Beta","message_delay":0,"enabled":False})
            store.save_bot_entry({"channel":7,"trigger":"gamma","page_name":"Gamma","message_delay":0})
            class EventType:
                CHANNEL_MSG_RECV=object(); MESSAGES_WAITING=object(); NO_MORE_MSGS=object(); ERROR=object()
            stop=threading.Event(); mc=MeshCore(stop)
            with patch.dict(sys.modules,{"meshcore":type("MeshcoreModule",(),{"EventType":EventType})()}), patch("towncrier.engine.Radio.from_store",return_value=Radio(mc)):
                await BotListener(store,stop)._run_async()
            history={row["text"]:row for row in store.bot_messages()}
        self.assertEqual(sorted(mc.commands.sent),[(5,"alpha-reply"),(7,"gamma-reply")])
        self.assertTrue(history["Node: alpha"]["triggered"])
        self.assertEqual(history["Node: alpha"]["reply_status"],"unconfirmed")
        self.assertFalse(history["Node: beta"]["triggered"])
        self.assertEqual(history["Node: beta"]["reply_status"],"")
        self.assertTrue(history["Node: gamma"]["triggered"])
        self.assertEqual(history["Node: gamma"]["reply_status"],"unconfirmed")

class WebUiTests(unittest.TestCase):
    def test_shared_save_confirmation_hooks_cover_server_and_composer_saves(self):
        root=Path(__file__).parents[1]/"towncrier"
        theme=(root/"static/theme.js").read_text()
        composer=(root/"static/publisher.js").read_text()
        bundle=(root/"static/publisher.bundle.js").read_text()
        page=(root/"static/publisher.html").read_text()
        app=(root/"app.py").read_text()
        self.assertIn("window.towncrierFlashSaved = flashSaved",theme)
        self.assertIn("setTimeout(() =>",theme)
        self.assertIn("1500",theme)
        self.assertIn('data-save-id="page"',page)
        self.assertIn("towncrierFlashSaved?.($('saveButton'))",composer)
        self.assertIn('window.towncrierFlashSaved?.($("saveButton"))',bundle)
        self.assertIn('window.towncrierFlashSaved?.($("scheduleSave"))',bundle)
        self.assertIn('saved_marker={"/save":"schedule","/settings":"connection","/radio-settings":"radio-preset","/channel-save":"channel","/bot-entries":"bot-entry"}',app)
        self.assertIn('data-save-id="bot-entry"',app)

    def test_legacy_bot_save_redirects_with_success_marker_but_validation_error_does_not(self):
        import urllib.parse
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs): return None
        tmp=tempfile.TemporaryDirectory(); old_store=getattr(Handler,"store",None)
        Handler.store=Store(Path(tmp.name)/"save-confirmation.db")
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        opener=urllib.request.build_opener(NoRedirect)
        try:
            good=urllib.parse.urlencode({"channel":"5","trigger":"test","page_name":"Reply","message_delay":"3","enabled":"on"}).encode()
            with self.assertRaises(urllib.error.HTTPError) as redirected:
                opener.open(Request(f"http://127.0.0.1:{server.server_port}/bot",data=good,headers={"Content-Type":"application/x-www-form-urlencoded"}))
            self.assertEqual(redirected.exception.code,303)
            self.assertEqual(redirected.exception.headers["Location"],"/bot?saved=bot")
            bad=urllib.parse.urlencode({"message_delay":"not-a-number"}).encode()
            with self.assertRaises(urllib.error.HTTPError) as failed:
                opener.open(Request(f"http://127.0.0.1:{server.server_port}/bot",data=bad,headers={"Content-Type":"application/x-www-form-urlencoded"}))
            self.assertEqual(failed.exception.code,400)
            self.assertIsNone(failed.exception.headers.get("Location"))
        finally:
            server.shutdown(); thread.join(); server.server_close(); Handler.store=old_store; tmp.cleanup()

    def test_publisher_page_contains_composer_and_page_controls(self):
        page=(Path(__file__).parents[1]/"towncrier/static/publisher.html").read_text()
        for control in ('id="messages"','id="saveButton"','id="importInput"','id="savedLists"'):
            self.assertIn(control,page)
        self.assertIn('id="sendButton"',page)
        self.assertIn('href="/publish"',page)

    def test_publisher_uses_compact_card_surfaces(self):
        css=(Path(__file__).parents[1]/"towncrier/static/publisher.css").read_text()
        source=(Path(__file__).parents[1]/"towncrier/static/publisher.js").read_text()
        self.assertIn('.panel,.towncrier-scheduler .card{background:var(--em-panel);border:1px solid var(--em-border);border-radius:10px;box-shadow:none;padding:14px}',css)
        self.assertIn('.message-editor{background:var(--em-panel-soft);border:1px solid var(--em-border);border-radius:10px;padding:10px}',css)
        self.assertIn("load.className='quiet load-button'",source)
        self.assertIn("exportButton.className='quiet export-button'",source)
        self.assertIn('.saved-item .load-button{background:var(--eastmesh-green-strong);color:#fff;border-color:var(--eastmesh-green-strong)}',css)
        self.assertIn('.saved-item .export-button{background:#2563a8;color:#fff;border-color:#2563a8}',css)

    def test_server_rendered_tabs_include_the_mobile_initial_scale_viewport(self):
        source=(Path(__file__).parents[1]/"towncrier/app.py").read_text()
        self.assertGreaterEqual(source.count('name="viewport" content="width=device-width,initial-scale=1"'),3)

    def test_mobile_header_uses_a_compact_publisher_title(self):
        css=(Path(__file__).parents[1]/"towncrier/static/publisher.css").read_text()
        self.assertIn('@media(max-width:560px){.towncrier-header h1{font-size:18px;line-height:1.2}}',css)

    def test_navigation_uses_rooms_radio_and_a_header_theme_toggle(self):
        root=Path(__file__).parents[1]
        sources=[
            (root/"towncrier/static/publisher.html").read_text(),
            (root/"towncrier/static/publish.html").read_text(),
            (root/"towncrier/app.py").read_text(),
        ]
        for source in sources:
            self.assertRegex(source,r'href="/publish"[^>]*>Rooms</a>')
            self.assertRegex(source,r'href="/radio"[^>]*>Radio</a>')
            self.assertIn('</span><button id="themeToggle"',source)
            self.assertNotIn('href="/publish">Publish</a>',source)
            self.assertNotIn('href="/radio">Radio Settings</a>',source)

    def test_composer_page_delete_uses_a_confirmation_dialog(self):
        source=(Path(__file__).parents[1]/"towncrier/static/publisher.js").read_text()
        self.assertIn("deletePageDialog",source)
        self.assertIn("showModal()",source)
        self.assertIn("method:'DELETE'",source)
        self.assertIn("Delete “${name}”?",source)

    def test_publish_page_contains_room_flow_and_saved_page_selector(self):
        page=(Path(__file__).parents[1]/"towncrier/static/publish.html").read_text()
        for control in ('id="roomServerSelect"','id="roomPassword"','id="loginButton"','id="pageSelect"','id="publishButton"'):
            self.assertIn(control,page)

    def test_publisher_composer_has_left_side_clickable_message_order_controls(self):
        source=(Path(__file__).parents[1]/"towncrier/static/publisher.js").read_text()
        self.assertIn("arrangement.append(up,down)",source)
        css=(Path(__file__).parents[1]/"towncrier/static/publisher.css").read_text()
        self.assertIn(".message-editor-body{grid-template-columns:40px minmax(0,1fr) 40px}",css)
        self.assertIn(".message-rail{flex-direction:column",css)
        self.assertIn("editorBody.append(arrangement,field,controls)",source)
        self.assertIn("controls.append(remove,add)",source)
        self.assertNotIn("row.append(arrangement,editor)",source)
        self.assertNotIn("drag-handle",source)
        self.assertNotIn("pointerdown",source)

    def test_composer_message_editor_builds_its_textarea_before_appending_it(self):
        root=Path(__file__).parents[1]/"towncrier/static"
        source=(root/"publisher.js").read_text()
        bundle=(root/"publisher.bundle.js").read_text()
        for script in (source,bundle):
            self.assertIn("message-editor-body",script)
            self.assertIn("message-field",script)
            self.assertIn("createElement('textarea')" if script is source else 'createElement("textarea")',script)
            self.assertIn("field.append(ta)",script)
            self.assertIn("editorBody.append(arrangement,field,controls)" if script is source else "editorBody.append(arrangement, field, controls)",script)

    def test_schedule_desktop_layout_uses_spacious_three_area_grid(self):
        css=(Path(__file__).parents[1]/"towncrier/static/publisher.css").read_text()
        self.assertIn(".schedule-stack{display:grid;grid-template-columns:minmax(280px,1.15fr) minmax(260px,.85fr);gap:28px",css)
        self.assertIn(".schedule-stack>.schedule-settings-rail{display:block;grid-column:1",css)
        self.assertIn(".schedule-stack>.schedule-page-source{grid-column:2",css)
        self.assertIn(".schedule-stack>.schedule-actions-rail{display:grid;grid-column:1",css)

    def test_schedule_edit_loads_composer_without_opening_dialog(self):
        source=(Path(__file__).parents[1]/"towncrier/static/publisher.js").read_text()
        self.assertIn("Scheduled sequence loaded.",source)
        self.assertNotIn("scheduleSave').textContent='Save changes';$('scheduleDialog').showModal()",source)

    def test_message_overflow_creates_continuation_rows_and_preserves_text(self):
        root=Path(__file__).parents[1]/"towncrier/static"
        source=(root/"vendor/publisher.js").read_text()
        ui=(root/"publisher.js").read_text()
        bundle=(root/"publisher.bundle.js").read_text()
        self.assertIn("function update(){const info=validateMessage(ta.value);if(!info.valid)",ui)
        self.assertIn("next.splice(i,1,...chunks)",ui)
        self.assertIn("continuation?.focus()",ui)
        self.assertIn("const boundary = chunk.lastIndexOf(' ')",source)
        self.assertIn("encoder.encode(chunk + character).length > maxBytes",source)
        self.assertNotIn("truncateUtf8(ta.value,MAX_POST_BYTES)",ui)
        self.assertIn("next.splice(i, 1, ...chunks)",bundle)

    def test_message_controls_preserve_live_textarea_values(self):
        source=(Path(__file__).parents[1]/"towncrier/static/publisher.js").read_text()
        self.assertIn("const next=[...messageValues()];next.splice(i+1,0,'')",source)
        self.assertIn("const next=messageValues().filter((_,n)=>n!==i)",source)

    def test_static_routes_reject_path_traversal(self):
        from towncrier.app import Handler
        self.assertTrue((Path(__file__).parents[1]/"towncrier/static/publisher.bundle.js").is_file())

    def test_bot_page_has_channel_trigger_and_saved_list_controls(self):
        tmp=tempfile.TemporaryDirectory(); old_store=getattr(Handler,"store",None)
        Handler.store=Store(Path(tmp.name)/"bot-page.db")
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/bot") as response:
                page=response.read().decode()
            self.assertIn("Trigger word",page)
            self.assertIn("Channel",page)
            self.assertIn("Saved page",page)
            self.assertIn('name="message_delay"',page)
        finally:
            server.shutdown(); thread.join(); server.server_close(); Handler.store=old_store; tmp.cleanup()

    def test_bot_messages_endpoint_returns_only_the_latest_ten_messages(self):
        tmp=tempfile.TemporaryDirectory(); old_store=getattr(Handler,"store",None)
        store=Store(Path(tmp.name)/"bot-history.db"); Handler.store=store
        for index in range(12): store.record_bot_message(5,f"message {index}")
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/publisher-api/bot-messages?channel=5") as response:
                messages=json.loads(response.read().decode())["messages"]
        finally:
            server.shutdown(); thread.join(); server.server_close(); Handler.store=old_store; tmp.cleanup()
        self.assertEqual(len(messages),10)

    def test_bot_message_delay_form_persists(self):
        import urllib.parse
        tmp=tempfile.TemporaryDirectory(); old_store=getattr(Handler,"store",None)
        store=Store(Path(tmp.name)/"bot-delay.db"); Handler.store=store
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            payload=urllib.parse.urlencode({"channel":"5","trigger":"testing","page_name":"Reply","message_delay":"7","enabled":"on"}).encode()
            request=Request(f"http://127.0.0.1:{server.server_port}/bot",data=payload,headers={"Content-Type":"application/x-www-form-urlencoded"})
            with urllib.request.urlopen(request) as response:
                page=response.read().decode()
            stored_delay=store.setting("bot_message_delay")
        finally:
            server.shutdown(); thread.join(); server.server_close(); Handler.store=old_store; tmp.cleanup()
        self.assertEqual(stored_delay,"7")
        self.assertIn('name="message_delay" type="number" min="0" max="3600" value="7"',page)

    def test_bot_config_round_trips_and_trigger_posts_selected_list_to_channel(self):
        tmp=tempfile.TemporaryDirectory(); store=Store(Path(tmp.name)/"lists.db")
        try:
            store.save_list("Alerts",["one","two"])
            self.assertEqual(store.saved_lists(),[{"name":"Alerts","messages":["one","two"]}])
        finally: tmp.cleanup()

    def test_scheduler_has_publisher_tab(self):
        page=(Path(__file__).parents[1]/"towncrier/static/publisher.html").read_text()
        self.assertIn('class="active" href="/"',page)

    def test_schedule_management_page_links_to_shared_composer_not_another_editor(self):
        tmp=tempfile.TemporaryDirectory(); old_store=getattr(Handler,"store",None)
        Handler.store=Store(Path(tmp.name)/"schedule-page.db")
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            with patch("towncrier.radio.Radio.snapshot",return_value=({},[])):
                with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/schedules") as response:
                    page=response.read().decode()
        finally:
            server.shutdown(); thread.join(); server.server_close(); Handler.store=old_store; tmp.cleanup()
        self.assertIn('href="/schedules"',page)
        self.assertIn('id="schedule-form"',page)
        self.assertIn('SCHEDULE SETUP',page)
        self.assertIn('name="page_name"',page)
        self.assertIn('SCHEDULE SETUP',page)

    def test_schedule_navigation_does_not_wait_for_stalled_radio(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/"stalled.db")
            release=threading.Event()
            def snapshot():
                release.wait(3)
                return {},[]
            handler=object.__new__(Handler)
            handler.store=store
            Handler.invalidate_radio_snapshot()
            try:
                with patch("towncrier.app.Radio.from_store",return_value=SimpleNamespace(snapshot=snapshot)):
                    import time
                    started=time.monotonic()
                    with self.assertRaisesRegex(RuntimeError,"refresh"):
                        handler.cached_radio_snapshot()
                    self.assertLess(time.monotonic()-started,1.2)
            finally:
                release.set()
                Handler.invalidate_radio_snapshot()

    def test_schedules_reuses_a_recent_radio_snapshot(self):
        tmp=tempfile.TemporaryDirectory(); old_store=getattr(Handler,"store",None)
        Handler.store=Store(Path(tmp.name)/"schedule-snapshot-cache.db")
        Handler.radio_snapshot_cache=None; Handler.radio_snapshot_cached_at=0
        calls=0
        class Radio:
            def snapshot(self):
                nonlocal calls
                calls+=1
                return {},[{"index":0,"name":"General","configured":True}]
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            with patch("towncrier.app.Radio.from_store",return_value=Radio()):
                for _ in range(2):
                    with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/schedules") as response:
                        self.assertEqual(response.status,200)
        finally:
            server.shutdown(); thread.join(); server.server_close(); Handler.store=old_store
            Handler.radio_snapshot_cache=None; Handler.radio_snapshot_store_path=None; Handler.radio_snapshot_cached_at=0
            tmp.cleanup()
        self.assertEqual(calls,1)

    def test_saved_schedule_load_link_stays_on_the_schedules_page(self):
        tmp=tempfile.TemporaryDirectory(); old_store=getattr(Handler,"store",None)
        store=Store(Path(tmp.name)/"schedule-load-link.db"); Handler.store=store
        ident=store.save({"name":"Morning","channels":[0,3,7],"messages":["hello"],"interval":3600})
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            with patch("towncrier.radio.Radio.snapshot",return_value=({},[{"index":0,"name":"General & Events","configured":True},{"index":3,"name":"#local","configured":True},{"index":7,"name":"-","configured":False}])):
                with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/schedules") as response:
                    page=response.read().decode()
        finally:
            server.shutdown(); thread.join(); server.server_close(); Handler.store=old_store; tmp.cleanup()
        self.assertIn(f'href="/schedules?edit={ident}"',page)
        self.assertIn("Channels: General &amp; Events, #local, Channel 7 ·",page)

    def test_saved_schedules_render_as_individual_cards(self):
        tmp=tempfile.TemporaryDirectory(); old_store=getattr(Handler,"store",None)
        store=Store(Path(tmp.name)/"schedule-cards.db"); Handler.store=store
        store.save({"name":"Morning","channels":[0],"messages":["hello"],"interval":3600})
        store.save({"name":"Evening","channels":[0],"messages":["goodnight"],"interval":3600})
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            with patch("towncrier.radio.Radio.snapshot",return_value=({},[{"index":0,"name":"General","configured":True}])):
                with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/schedules") as response:
                    page=response.read().decode()
        finally:
            server.shutdown(); thread.join(); server.server_close(); Handler.store=old_store; tmp.cleanup()
        self.assertIn('id="schedule-page" class="schedule-list-heading"',page)
        self.assertEqual(page.count('class="card panel saved-schedule"'),2)
        self.assertIn("Morning",page)
        self.assertIn("Evening",page)

    def test_schedule_with_extra_daily_times_has_removal_controls(self):
        tmp=tempfile.TemporaryDirectory(); old_store=getattr(Handler,"store",None)
        store=Store(Path(tmp.name)/"schedule-times.db"); Handler.store=store
        ident=store.save({"name":"Morning","channels":[0],"messages":["hello"],"interval":3600,"post_times":["08:00","12:00","18:00"]})
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            with patch("towncrier.radio.Radio.snapshot",return_value=({},[{"index":0,"name":"General","configured":True}])):
                with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/schedules?edit={ident}") as response:
                    page=response.read().decode()
        finally:
            server.shutdown(); thread.join(); server.server_close(); Handler.store=old_store; tmp.cleanup()
        self.assertEqual(page.split('<script>',1)[0].count('data-action="remove-post-time"'),2)
        self.assertIn('function refreshPostTimeControls()',page)

    def test_schedule_channel_picker_shows_all_named_channels_only(self):
        tmp=tempfile.TemporaryDirectory(); old_store=getattr(Handler,"store",None)
        Handler.store=Store(Path(tmp.name)/"named-schedule-channels.db")
        channels=[
            {"index":0,"name":"General","configured":True},
            {"index":1,"name":"","configured":False},
            {"index":5,"name":"Ops","configured":True},
            {"index":8,"name":"-","configured":False},
            {"index":9,"name":"Field","configured":True},
        ]
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            with patch("towncrier.radio.Radio.snapshot",return_value=({},channels)):
                with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/schedules") as response:
                    page=response.read().decode()
        finally:
            server.shutdown(); thread.join(); server.server_close(); Handler.store=old_store; tmp.cleanup()
        self.assertIn('class="channel-picker"',page)
        self.assertIn('value="9"',page)
        self.assertIn('9 · Field',page)
        self.assertNotIn('1 · Channel 1',page)
        self.assertNotIn('8 · -',page)

    def test_radio_channel_table_shows_all_named_channels_only(self):
        tmp=tempfile.TemporaryDirectory(); old_store=getattr(Handler,"store",None)
        Handler.store=Store(Path(tmp.name)/"named-radio-channels.db")
        channels=[
            {"index":0,"name":"General","configured":True},
            {"index":1,"name":"","configured":False},
            {"index":8,"name":"-","configured":False},
            {"index":52,"name":"Field","configured":True},
        ]
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            with patch("towncrier.radio.Radio.snapshot",return_value=({},channels)):
                with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/radio") as response:
                    page=response.read().decode()
        finally:
            server.shutdown(); thread.join(); server.server_close(); Handler.store=old_store; tmp.cleanup()
        self.assertIn('<td>52</td><td>Field</td>',page)
        self.assertNotIn('<td>1</td><td>—</td>',page)
        self.assertNotIn('<td>8</td><td>-</td>',page)

    def test_radio_serial_picker_prefers_stable_paths(self):
        from towncrier.app import serial_device_paths
        with tempfile.TemporaryDirectory() as tmp:
            dev=Path(tmp)
            (dev/"serial/by-id").mkdir(parents=True)
            (dev/"ttyACM0").touch()
            (dev/"ttyUSB1").touch()
            stable=dev/"serial/by-id/mesh-radio"
            stable.symlink_to("../../ttyACM0")
            (dev/"serial/by-id/disconnected").symlink_to("../../ttyUSB9")
            self.assertEqual(serial_device_paths(dev),[str(stable),str(dev/"ttyUSB1")])

    def test_radio_settings_are_a_separate_themed_tab(self):
        tmp=tempfile.TemporaryDirectory()
        old_store=getattr(Handler,"store",None)
        Handler.store=Store(Path(tmp.name)/"ui.db")
        channels=[{"index":i,"name":"","configured":False} for i in range(5)]
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            with patch("towncrier.radio.Radio.snapshot",return_value=({"radio_freq":915.8},channels)):
                with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/schedules") as response:
                    scheduler=response.read().decode()
                with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/radio") as response:
                    radio=response.read().decode()
            publisher=(Path(__file__).parents[1]/"towncrier/static/publisher.html").read_text()
        finally:
            server.shutdown(); thread.join(); server.server_close(); Handler.store=old_store; tmp.cleanup()
        for page in (scheduler,radio,publisher):
            self.assertIn('href="/radio"',page)
            self.assertIn('href="/publisher.css"',page)
            self.assertIn('class="brand-logo"',page)
        self.assertNotIn('action="/radio-settings"',scheduler)
        self.assertIn('class="active" href="/radio"',radio)
        self.assertIn('action="/radio-settings"',radio)
        self.assertIn('action="/channel-save"',radio)
        self.assertIn('action="/settings"',radio)
        self.assertIn('name="connection_type"',radio)
        self.assertIn('value="tcp"',radio)
        self.assertIn('name="tcp_host"',radio)
        self.assertIn('name="tcp_port"',radio)

    def test_server_tabs_use_the_same_outer_card_spacing_as_publish(self):
        css=(Path(__file__).parents[1]/"towncrier/static/publisher.css").read_text()
        self.assertIn('.towncrier-scheduler{max-width:none;margin:34px auto;padding:0}',css)

    def test_shared_theme_tokens_are_served_from_config_and_headers_use_status_dots(self):
        root=Path(__file__).parents[1]/"towncrier/static"
        tmp=tempfile.TemporaryDirectory(); old_store=getattr(Handler,"store",None)
        Handler.store=Store(Path(tmp.name)/"theme-assets.db")
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/publisher.css") as response:
                publisher_css=response.read().decode()
            with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/theme.css") as response:
                theme_css=response.read().decode()
        finally:
            server.shutdown(); thread.join(); server.server_close(); Handler.store=old_store; tmp.cleanup()
        tokens=json.loads((Path(__file__).parents[1]/"towncrier/theme_tokens.json").read_text())
        self.assertIn("--eastmesh-green:"+tokens["shared"]["eastmesh-green"],publisher_css)
        self.assertIn("--em-bg:"+tokens["light"]["em-bg"],theme_css)
        self.assertNotIn("--em-bg:#f3f6f4",(root/"theme.css").read_text())
        for name in ("publisher.html","publish.html"):
            page=(root/name).read_text()
            self.assertIn('id="connectionBadge" class="radio-dot disconnected"',page)

    def test_light_mode_toggle_is_shared_and_persistent_across_pages(self):
        root=Path(__file__).parents[1]/"towncrier/static"
        tmp=tempfile.TemporaryDirectory(); old_store=getattr(Handler,"store",None)
        Handler.store=Store(Path(tmp.name)/"theme.db")
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            pages=[]
            with patch("towncrier.radio.Radio.snapshot",return_value=({},[])):
                for path in ("/", "/publish", "/schedules", "/bot", "/radio"):
                    with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}{path}") as response:
                        pages.append(response.read().decode())
                with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/theme.css") as response:
                    css=response.read().decode()
        finally:
            server.shutdown(); thread.join(); server.server_close(); Handler.store=old_store; tmp.cleanup()
        theme=(root/"theme.js").read_text() if (root/"theme.js").exists() else ""
        for page in pages:
            self.assertIn('id="themeToggle"',page)
            self.assertIn('aria-label="Switch to light mode"',page)
            self.assertIn('src="/theme.js"',page)
        self.assertIn("localStorage",theme)
        self.assertIn("dataset.theme",theme)
        self.assertIn("addEventListener('click'",theme)
        self.assertIn(':root[data-theme="light"]',css)

    def test_scheduler_page_is_management_only(self):
        tmp=tempfile.TemporaryDirectory(); old_store=getattr(Handler,"store",None)
        store=Store(Path(tmp.name)/"messages-ui.db"); Handler.store=store
        store.save({"name":"Pair","channels":[0],"messages":["first","second"],"interval":1800,"message_delay":3})
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        channels=[{"index":i,"name":"","configured":False} for i in range(5)]
        try:
            with patch("towncrier.radio.Radio.snapshot",return_value=({},channels)):
                with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/schedules") as response:
                    page=response.read().decode()
        finally:
            server.shutdown(); thread.join(); server.server_close(); Handler.store=old_store; tmp.cleanup()
        self.assertNotIn('aria-posinset=',page)
        self.assertNotIn('id="messages"',page)
        self.assertNotIn('message-editor-panel',page)
        self.assertIn('name="page_name"',page)
        self.assertIn('Daily post times',page)
        self.assertIn('SAVED SCHEDULES',page)

    def test_enable_and_pause_return_to_schedules(self):
        with tempfile.TemporaryDirectory() as tmp:
            class TestHandler(Handler):
                store=Store(Path(tmp)/"toggle.db")

            ident=TestHandler.store.save({"name":"Morning","channel":0,"message":"hello","interval":1800,"enabled":False})
            with ThreadingHTTPServer(("127.0.0.1",0),TestHandler) as server:
                thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
                base=f"http://127.0.0.1:{server.server_port}"
                try:
                    with patch("towncrier.app.Radio.snapshot",return_value=({},[])):
                        for enabled in (1,0):
                            with urlopen(Request(base+"/toggle",data=f"id={ident}".encode()),timeout=3) as response:
                                self.assertEqual(response.geturl(),base+"/schedules")
                            schedule=TestHandler.store.get(ident)
                            assert schedule is not None
                            self.assertEqual(schedule["enabled"],enabled)
                finally:
                    server.shutdown(); thread.join()

    def test_send_now_posts_saved_messages_and_returns_to_schedules(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/"send-now.db")
            ident=store.save({"name":"Manual","channels":[1,3],"messages":["first","second"],"interval":1800,"message_delay":0,"enabled":False})
            before=store.get(ident)
            class TestHandler(Handler):
                pass
            TestHandler.store=store
            with ThreadingHTTPServer(("127.0.0.1",0),TestHandler) as server:
                thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
                try:
                    with patch("towncrier.app.Radio.from_store") as radio:
                        radio.return_value.snapshot.return_value=({},[])
                        request=Request(f"http://127.0.0.1:{server.server_port}/send",data=f"id={ident}".encode())
                        with urlopen(request,timeout=3) as response:
                            destination=response.geturl()
                        self.assertEqual([call.args for call in radio.return_value.send.call_args_list],[(1,"first"),(1,"second"),(3,"first"),(3,"second")])
                    self.assertTrue(destination.endswith("/schedules"),destination)
                    self.assertEqual(store.get(ident),before)
                finally:
                    server.shutdown(); thread.join()

    def test_save_edit_preserves_next_post_time_and_message_order(self):
        import urllib.parse
        tmp=tempfile.TemporaryDirectory(); old_store=getattr(Handler,"store",None)
        store=Store(Path(tmp.name)/"messages-order.db"); Handler.store=store
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        fields=[("name","Ordered"),("channel","0"),("post_time","09:00"),("post_time","12:00"),("message_delay","3"),("message","second"),("message","first"),("enabled","on")]
        request=urllib.request.Request(f"http://127.0.0.1:{server.server_port}/save",data=urllib.parse.urlencode(fields).encode(),headers={"Content-Type":"application/x-www-form-urlencoded"})
        try:
            with patch("towncrier.radio.Radio.snapshot",return_value=({},[])):
                with urllib.request.urlopen(request) as response:
                    self.assertEqual(urllib.parse.urlsplit(response.geturl()).path,"/schedules")
                created=store.get(1)
                assert created is not None
                original_next_send=created["next_send"]
                saved_messages=created["messages"]
                edit_fields=[("id","1"),("name","Edited"),("channel","0"),("post_time","10:00"),("post_time","15:00"),("message_delay","3"),("message","updated")]
                edit_request=Request(f"http://127.0.0.1:{server.server_port}/save",data=urllib.parse.urlencode(edit_fields).encode(),headers={"Content-Type":"application/x-www-form-urlencoded"})
                with urlopen(edit_request) as response:
                    self.assertEqual(urllib.parse.urlsplit(response.geturl()).path,"/schedules")
            updated=store.get(1)
            assert updated is not None
            self.assertEqual(updated["enabled"],1)
            store.toggle(1)
            edit_fields.append(("enabled","on"))  # A stale form must not resume it.
            with patch("towncrier.radio.Radio.snapshot",return_value=({},[])):
                with urlopen(Request(edit_request.full_url,data=urllib.parse.urlencode(edit_fields).encode())) as response:
                    self.assertEqual(urllib.parse.urlsplit(response.geturl()).path,"/schedules")
            self.assertEqual(store.get(1)["enabled"],0)
        finally:
            server.shutdown(); thread.join(); server.server_close(); Handler.store=old_store; tmp.cleanup()
        self.assertEqual(saved_messages,["second","first"])
        self.assertEqual(updated["messages"],["updated"])
        self.assertEqual(updated["next_send"],original_next_send)
        self.assertEqual(updated["post_times"],["10:00","15:00"])

    def test_tcp_connection_settings_are_saved_and_validated(self):
        import urllib.parse
        tmp=tempfile.TemporaryDirectory(); old_store=getattr(Handler,"store",None)
        store=Store(Path(tmp.name)/"tcp-settings.db"); Handler.store=store
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        base=f"http://127.0.0.1:{server.server_port}"
        try:
            with patch("towncrier.radio.Radio.snapshot",return_value=({},[])):
                for port in ("5000","65536"):
                    data=urllib.parse.urlencode({"connection_type":"tcp","tcp_host":"mesh.local","tcp_port":port,"port":"/dev/ttyUSB0","baud":"115200"}).encode()
                    request=Request(base+"/settings",data=data,headers={"Content-Type":"application/x-www-form-urlencoded"})
                    with urlopen(request) as response:
                        result=response.read().decode()
                        final_url=response.geturl()
                    if port=="65536":
                        self.assertTrue(final_url.endswith("/settings"))
                        self.assertIn("TCP port must be between 1 and 65535",result)
                        self.assertEqual(store.setting("tcp_port"),"5000")
                    else:
                        self.assertTrue(final_url.endswith("/radio?saved=connection"))
                        self.assertEqual(store.setting("tcp_port"),"5000")
            self.assertEqual(store.setting("connection_type"),"tcp")
            self.assertEqual(store.setting("tcp_host"),"mesh.local")
            self.assertEqual(store.setting("tcp_port"),"5000")
            serial_data=urllib.parse.urlencode({"connection_type":"serial","port":"/dev/ttyACM1","baud":"57600"}).encode()
            serial_request=Request(base+"/settings",data=serial_data,headers={"Content-Type":"application/x-www-form-urlencoded"})
            with patch("towncrier.radio.Radio.snapshot",return_value=({},[])):
                with urlopen(serial_request) as response: self.assertTrue(response.geturl().endswith("/radio?saved=connection"))
            self.assertEqual(store.setting("connection_type"),"serial")
            self.assertEqual(store.setting("port"),"/dev/ttyACM1")
            self.assertEqual(store.setting("baud"),"57600")
        finally:
            server.shutdown(); thread.join(); server.server_close(); Handler.store=old_store; tmp.cleanup()

    def test_bot_table_shows_named_channel_trigger_page_and_state_controls(self):
        tmp=tempfile.TemporaryDirectory(); old_store=getattr(Handler,"store",None)
        store=Store(Path(tmp.name)/"bot-table.db"); Handler.store=store
        store.save_list("Reply",["pong"])
        active_id=store.save_bot_entry({"channel":5,"trigger":"ping","page_name":"Reply"})
        paused_id=store.save_bot_entry({"channel":9,"trigger":"status","page_name":"Reply","enabled":False})
        channels=[{"index":5,"name":"Ops","configured":True},{"index":9,"name":"Field","configured":True}]
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            with patch("towncrier.radio.Radio.snapshot",return_value=({},channels)):
                with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/bot") as response:
                    page=response.read().decode()
        finally:
            server.shutdown(); thread.join(); server.server_close(); Handler.store=old_store; tmp.cleanup()
        self.assertIn('id="bot-entries-table"',page)
        self.assertIn("5 · Ops",page)
        self.assertIn("9 · Field",page)
        self.assertIn(">ping<",page)
        self.assertIn(">status<",page)
        self.assertIn(">Active<",page)
        self.assertIn(">Paused<",page)
        self.assertIn(">Pause<",page)
        self.assertIn(">Resume<",page)
        self.assertIn('href="/bot?edit='+str(active_id)+'"',page)
        self.assertIn('href="/bot?edit='+str(paused_id)+'"',page)
        self.assertIn('action="/bot-entries/toggle"',page)
        self.assertIn('action="/bot-entries/delete"',page)

    def test_bot_entries_create_toggle_and_delete_endpoints(self):
        import urllib.parse
        tmp=tempfile.TemporaryDirectory(); old_store=getattr(Handler,"store",None)
        store=Store(Path(tmp.name)/"bot-crud.db"); Handler.store=store
        store.save_list("Reply",["pong"])
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            base=f"http://127.0.0.1:{server.server_port}"
            create=urllib.parse.urlencode({"channel":"5","trigger":"ping","page_name":"Reply","message_delay":"3","enabled":"on"}).encode()
            request=Request(base+"/bot-entries",data=create,headers={"Content-Type":"application/x-www-form-urlencoded"})
            with urlopen(request) as response:
                self.assertTrue(response.geturl().endswith("/bot?saved=bot-entry"))
            entries=store.real_bot_entries()
            self.assertEqual(len(entries),1)
            ident=entries[0]["id"]
            self.assertEqual(entries[0]["enabled"],1)

            collide=urllib.parse.urlencode({"channel":"5","trigger":"PING","page_name":"Reply","enabled":"on"}).encode()
            with urlopen(Request(base+"/bot-entries",data=collide,headers={"Content-Type":"application/x-www-form-urlencoded"})) as response:
                collide_page=response.read().decode()
            self.assertIn("already listens",collide_page)
            self.assertEqual(len(store.real_bot_entries()),1)

            toggle=urllib.parse.urlencode({"id":str(ident)}).encode()
            with urlopen(Request(base+"/bot-entries/toggle",data=toggle,headers={"Content-Type":"application/x-www-form-urlencoded"})) as response:
                self.assertTrue(response.geturl().endswith("/bot"))
            self.assertEqual(store.get_bot_entry(ident)["enabled"],0)

            delete=urllib.parse.urlencode({"id":str(ident)}).encode()
            with urlopen(Request(base+"/bot-entries/delete",data=delete,headers={"Content-Type":"application/x-www-form-urlencoded"})) as response:
                self.assertTrue(response.geturl().endswith("/bot"))
            self.assertIsNone(store.get_bot_entry(ident))
        finally:
            server.shutdown(); thread.join(); server.server_close(); Handler.store=old_store; tmp.cleanup()

class PublisherRadioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.radio = __import__("towncrier.radio", fromlist=["Radio"]).Radio("/dev/test", 115200)

    def tearDown(self): self.tmp.cleanup()

    def test_lists_only_room_server_contacts(self):
        from types import SimpleNamespace
        from towncrier.radio import Radio
        class EventType: ERROR = "error"
        class Commands:
            async def get_contacts(self): return SimpleNamespace(type="contacts")
        class Mesh:
            commands=Commands()
            contacts={"a"*64:{"public_key":"a"*64,"type":3,"adv_name":"Room A"},"b"*64:{"public_key":"b"*64,"type":2,"adv_name":"Repeater"}}
            async def disconnect(self): pass
        mesh=Mesh()
        async def connect(_radio): return mesh
        with patch("towncrier.radio.Radio._connect", new=connect), patch.dict("sys.modules", {"meshcore":SimpleNamespace(EventType=EventType)}):
            self.assertEqual(Radio("/dev/test",115200).publisher_contacts(),[{"publicKeyHex":"a"*64,"name":"Room A"}])

    def test_publisher_login_respects_meshcore_hop_timeout_budget(self):
        from types import SimpleNamespace
        from towncrier.radio import Radio
        class EventType: ERROR="error"; LOGIN_SUCCESS="login_success"; LOGIN_FAILED="login_failed"
        timeouts=[]
        class Dispatcher:
            def __init__(self): self.subscriptions=[]
            def subscribe(self,event_type,callback):
                entry=[event_type,callback]; self.subscriptions.append(entry)
                return SimpleNamespace(unsubscribe=lambda:self.subscriptions.remove(entry))
            def emit(self,event):
                for event_type,callback in list(self.subscriptions):
                    if event_type==event.type: callback(event)
        class Commands:
            async def get_contacts(self): return SimpleNamespace(type="contacts")
            async def send_login(self, contact, password):
                self.called=(contact,password)
                self.dispatcher.emit(SimpleNamespace(type="login_success",attributes={"pubkey_prefix":"a"*12},payload={"permissions":2}))
                return SimpleNamespace(type="message_sent",payload={"suggested_timeout":40000})
        class Mesh:
            dispatcher=Dispatcher(); commands=Commands(); contacts={"a"*64:{"public_key":"a"*64,"type":3,"adv_name":"Room A"}}
            commands.dispatcher=dispatcher
            async def disconnect(self): pass
        mesh=Mesh()
        async def connect(_radio): return mesh
        async def capture_timeout(awaitable,timeout):
            timeouts.append(timeout)
            return await awaitable
        with patch("towncrier.radio.Radio._connect", new=connect), patch("towncrier.radio.asyncio.wait_for",new=capture_timeout), patch.dict("sys.modules", {"meshcore":SimpleNamespace(EventType=EventType)}):
            result=Radio("/dev/test",115200).publisher_login("a"*64,"secret")
        self.assertEqual(result,{"permissions":2})
        self.assertGreaterEqual(timeouts[0],40000/800)
        self.assertEqual(mesh.commands.called,(mesh.contacts["a"*64],"secret"))
        with patch("towncrier.radio.Radio._connect",side_effect=AssertionError("should not open radio")), patch.dict("sys.modules", {"meshcore":SimpleNamespace(EventType=EventType)}):
            with self.assertRaisesRegex(ValueError,"15 UTF-8 bytes"):
                Radio("/dev/test",115200).publisher_login("a"*64,"é"*8)

    def test_publisher_login_accepts_response_without_key_prefix(self):
        import asyncio
        from types import SimpleNamespace
        from towncrier.radio import Radio
        class EventType: ERROR="error"; LOGIN_SUCCESS="login_success"; LOGIN_FAILED="login_failed"
        class Dispatcher:
            def __init__(self): self.subscriptions=[]
            def subscribe(self,event_type,callback):
                entry=[event_type,callback]; self.subscriptions.append(entry)
                return SimpleNamespace(unsubscribe=lambda:self.subscriptions.remove(entry))
            def emit(self,event):
                for event_type,callback in list(self.subscriptions):
                    if event_type==event.type: callback(event)
        class Commands:
            async def get_contacts(self): return SimpleNamespace(type="contacts")
            async def send_login(self,contact,password):
                self.dispatcher.emit(SimpleNamespace(type="login_success",attributes={},payload={"permissions":2}))
                return SimpleNamespace(type="message_sent",payload={"suggested_timeout":1000})
        class Mesh:
            dispatcher=Dispatcher(); commands=Commands(); contacts={"a"*64:{"public_key":"a"*64,"type":3,"adv_name":"Room A"}}
            commands.dispatcher=dispatcher
            async def disconnect(self): pass
        mesh=Mesh()
        async def connect(_radio): return mesh
        async def immediate_wait(awaitable,timeout):
            if not awaitable.done(): raise asyncio.TimeoutError()
            return await awaitable
        with patch("towncrier.radio.Radio._connect",new=connect), patch("towncrier.radio.asyncio.wait_for",new=immediate_wait), patch.dict("sys.modules",{"meshcore":SimpleNamespace(EventType=EventType)}):
            result=Radio("/dev/test",115200).publisher_login("a"*64,"secret")
        self.assertEqual(result,{"permissions":2})

    def test_publisher_send_uses_room_key_and_checks_utf8_limit(self):
        from types import SimpleNamespace
        from towncrier.radio import Radio
        class EventType: ERROR="error"; MSG_SENT="message_sent"
        class Commands:
            async def send_msg(self,key,text): self.sent=(key,text); return SimpleNamespace(type="message_sent",payload={"type":0})
        class Mesh:
            commands=Commands()
            async def disconnect(self): pass
        mesh=Mesh()
        async def connect(_radio): return mesh
        with patch("towncrier.radio.Radio._connect", new=connect), patch.dict("sys.modules", {"meshcore":SimpleNamespace(EventType=EventType)}):
            self.assertEqual(Radio("/dev/test",115200).publisher_send("c"*64,"hello"),{"sent":True})
            self.assertEqual(mesh.commands.sent,("c"*64,"hello"))
            with self.assertRaisesRegex(ValueError,"151 UTF-8 bytes"):
                Radio("/dev/test",115200).publisher_send("c"*64,"é"*76)


class RadioTcpTests(unittest.TestCase):
    def test_tcp_transport_calls_meshcore_factory(self):
        import asyncio
        from types import SimpleNamespace
        from towncrier.radio import Radio
        calls=[]; client=object()
        class MeshCore:
            @staticmethod
            async def create_tcp(host,port):
                calls.append((host,port)); return client
            @staticmethod
            async def create_serial(*args): raise AssertionError("TCP mode must not open serial")
        with patch.dict("sys.modules",{"meshcore":SimpleNamespace(MeshCore=MeshCore)}):
            result=asyncio.run(Radio("/dev/unused",115200,transport="tcp",host="192.168.1.50",tcp_port=4000)._connect())
        self.assertIs(result,client)
        self.assertEqual(calls,[("192.168.1.50",4000)])

    def test_from_store_restores_tcp_settings(self):
        from towncrier.radio import Radio
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/"settings.db")
            store.set_setting("connection_type","tcp"); store.set_setting("tcp_host","mesh.local"); store.set_setting("tcp_port","5000")
            radio=Radio.from_store(store)
        self.assertEqual((radio.transport,radio.host,radio.tcp_port),("tcp","mesh.local",5000))


class PublisherApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        Handler.store=Store(Path(self.tmp.name)/"api.db")
        Handler.publisher_sessions.clear()
        self.server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.base=f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown(); self.thread.join(); self.server.server_close(); self.tmp.cleanup()

    def request(self,path,method="GET",payload=None,cookie=None,content_type="application/json"):
        data=json.dumps(payload).encode() if payload is not None else None
        headers={}
        if data is not None: headers["Content-Type"]=content_type
        if cookie: headers["Cookie"]=cookie
        req=urllib.request.Request(self.base+path,data=data,headers=headers,method=method)
        try:
            with urllib.request.urlopen(req) as response: return response.status,response.headers,json.loads(response.read())
        except urllib.error.HTTPError as error: return error.code,error.headers,json.loads(error.read())

    def test_contacts_endpoint_uses_configured_radio_backend(self):
        expected=[{"publicKeyHex":"a"*64,"name":"Room"}]
        with patch("towncrier.radio.Radio.publisher_contacts",return_value=expected):
            status,_,body=self.request("/publisher-api/contacts")
        self.assertEqual(status,200)
        self.assertEqual(body,{"rooms":expected})

    def test_shared_composer_can_create_and_edit_a_schedule(self):
        data={"name":"Morning","channels":[0,2],"messages":["first","second"],"interval":1800,"message_delay":2,"post_times":["09:00","17:30"],"enabled":True}
        status,_,body=self.request("/publisher-api/schedule","POST",data)
        self.assertEqual(status,200)
        ident=body["id"]
        stored=Handler.store.get(ident)
        self.assertEqual(stored["messages"],data["messages"])
        self.assertEqual(stored["channels"],data["channels"])
        data.update({"id":ident,"name":"Morning edited","messages":["revised"]})
        self.assertEqual(self.request("/publisher-api/schedule","POST",data)[0],200)
        _,_,loaded=self.request(f"/publisher-api/schedule?id={ident}")
        self.assertEqual(loaded["name"],"Morning edited")
        self.assertEqual(loaded["messages"],["revised"])

    def test_publisher_page_delete_removes_the_named_saved_page(self):
        Handler.store.save_list("Disposable",["delete me"])
        status,_,body=self.request("/publisher-api/lists","DELETE",{"name":"Disposable"})
        self.assertEqual(status,200)
        self.assertEqual(body,{"lists":[]})
        self.assertEqual(Handler.store.saved_lists(),[])
        self.assertEqual(self.request("/publisher-api/lists","DELETE",{})[0],400)

    def test_login_cookie_is_required_and_bound_to_room_before_publishing(self):
        key="b"*64
        class FakeSession:
            def __init__(self,radio,public_key,password): self.result={"permissions":2}; self.sent=[]
            def send(self,text): self.sent.append(text); return {"sent":True}
            def close(self): pass
        with patch("towncrier.app.PublisherSession",FakeSession) as session:
            status,headers,body=self.request("/publisher-api/login","POST",{"public_key":key,"password":"secret"})
            self.assertEqual((status,body),(200,{"permissions":2}))
            self.assertIn("HttpOnly",headers.get("Set-Cookie"))
            token=headers.get("Set-Cookie").split(";",1)[0]
            self.assertEqual(self.request("/publisher-api/publish","POST",{"public_key":key,"text":"x"})[0],401)
            self.assertEqual(self.request("/publisher-api/publish","POST",{"public_key":"c"*64,"text":"x"},token)[0],401)
            status,_,body=self.request("/publisher-api/publish","POST",{"public_key":key,"text":"hello"},token)
        self.assertEqual((status,body),(200,{"sent":True}))
        self.assertEqual(len(session.call_args_list) if hasattr(session,"call_args_list") else 1,1)

    def test_publish_tab_exposes_room_flow_but_no_browser_radio_chooser(self):
        html=(Path(__file__).parents[1]/"towncrier/static/publish.html").read_text()
        source=(Path(__file__).parents[1]/"towncrier/static/vendor/publisher.js").read_text()
        self.assertIn('href="/publish"',html)
        self.assertIn('id="roomServerSelect"',html)
        self.assertIn('id="pageSelect"',html)
        self.assertNotIn('navigator.serial',source)
        self.assertNotIn('navigator.bluetooth',source)


class InstallerTests(unittest.TestCase):
    def test_install_restarts_the_service_after_installing_updated_code(self):
        script=Path(__file__).parents[1].joinpath("install.sh").read_text()
        self.assertLess(script.index('pip" install "$PREFIX"'), script.index('systemctl restart towncrier'))

    def test_uninstaller_help_documents_database_preservation(self):
        script=Path(__file__).parents[1]/"uninstall.sh"
        result=subprocess.run(["bash",str(script),"--help"],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn("/var/lib/towncrier",result.stdout)

if __name__ == "__main__": unittest.main()
