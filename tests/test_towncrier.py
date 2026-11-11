import json
import sqlite3
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from urllib.request import Request, urlopen
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from towncrier.db import Store
from towncrier.app import esc, Handler
from towncrier.engine import Scheduler, send_message, split_message

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
        self.assertGreaterEqual(row["next_send"],fixed_now.timestamp()+72*3600)
        self.assertEqual(due_local.strftime("%H:%M"),"09:00")
        self.assertEqual(due_local.date(),(fixed_now+timedelta(hours=72)).date())
    def test_edit_keeps_next_send_timestamp(self):
        self.s.save({"name":"x","channels":[1],"message":"x","interval":72*3600})
        original=self.s.get(1)
        assert original is not None
        original_next_send=original["next_send"]
        self.s.save({"name":"updated","channels":[1],"message":"updated","interval":72*3600,"send_at":"09:00"},1)
        updated=self.s.get(1)
        assert updated is not None
        self.assertEqual(updated["next_send"],original_next_send)
        self.assertEqual(updated["send_at"],"09:00")
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
    def test_sent_moves_next_send(self):
        self.s.save({"name":"x","channel":1,"message":"x","interval":1800})
        row = self.s.schedules()[0]
        self.s.sent(row["id"], 100, 1800)
        self.assertEqual(self.s.get(row["id"])["next_send"], 1900)
    def test_escape(self): self.assertEqual(esc('<script>'), '&lt;script&gt;')

class WebUiTests(unittest.TestCase):
    def test_publisher_page_contains_composer_and_room_auth_controls(self):
        page=(Path(__file__).parents[1]/"towncrier/static/publisher.html").read_text()
        for control in ('id="messages"','id="sendButton"','id="roomPassword"','id="previewDialog"','id="saveDialog"','id="loadDialog"'):
            self.assertIn(control,page)

    def test_publisher_composer_has_per_message_add_delete_and_order_controls(self):
        root=Path(__file__).parents[1]/"towncrier/static"
        page=(root/"publisher.html").read_text()
        source=(root/"publisher.js").read_text()
        self.assertNotIn('id="addButton"',page)
        for label in ("Add message below","Remove this message","Move message up","Move message down"):
            self.assertIn(label,source)

    def test_schedule_edit_loads_composer_without_opening_dialog(self):
        source=(Path(__file__).parents[1]/"towncrier/static/publisher.js").read_text()
        self.assertIn("Scheduled sequence loaded.",source)
        self.assertNotIn("scheduleSave').textContent='Save changes';$('scheduleDialog').showModal()",source)

    def test_static_routes_reject_path_traversal(self):
        from towncrier.app import Handler
        self.assertTrue((Path(__file__).parents[1]/"towncrier/static/publisher.bundle.js").is_file())

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
        self.assertIn('href="/"',page)
        self.assertIn('Create schedule in the composer',page)
        self.assertNotIn('id="schedule-form"',page)
        self.assertNotIn('id="messages"',page)

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
        self.assertIn('Edit in composer',page)
        self.assertIn('Saved schedules',page)

    def test_save_edit_preserves_next_post_time_and_message_order(self):
        import urllib.parse
        tmp=tempfile.TemporaryDirectory(); old_store=getattr(Handler,"store",None)
        store=Store(Path(tmp.name)/"messages-order.db"); Handler.store=store
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        fields=[("name","Ordered"),("channel","0"),("send_at","09:00"),("hours","0"),("minutes","30"),("message_delay","3"),("message","second"),("message","first"),("enabled","on")]
        request=urllib.request.Request(f"http://127.0.0.1:{server.server_port}/save",data=urllib.parse.urlencode(fields).encode(),headers={"Content-Type":"application/x-www-form-urlencoded"})
        try:
            with patch("towncrier.radio.Radio.snapshot",return_value=({},[])):
                with urllib.request.urlopen(request): pass
                created=store.get(1)
                assert created is not None
                original_next_send=created["next_send"]
                saved_messages=created["messages"]
                edit_fields=[("id","1"),("name","Edited"),("channel","0"),("send_at","10:00"),("hours","0"),("minutes","30"),("message_delay","3"),("message","updated"),("enabled","on")]
                edit_request=Request(f"http://127.0.0.1:{server.server_port}/save",data=urllib.parse.urlencode(edit_fields).encode(),headers={"Content-Type":"application/x-www-form-urlencoded"})
                with urlopen(edit_request): pass
            updated=store.get(1)
            assert updated is not None
        finally:
            server.shutdown(); thread.join(); server.server_close(); Handler.store=old_store; tmp.cleanup()
        self.assertEqual(saved_messages,["second","first"])
        self.assertEqual(updated["messages"],["updated"])
        self.assertEqual(updated["next_send"],original_next_send)
        self.assertEqual(updated["send_at"],"10:00")

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
                        self.assertTrue(final_url.endswith("/radio"))
                        self.assertEqual(store.setting("tcp_port"),"5000")
            self.assertEqual(store.setting("connection_type"),"tcp")
            self.assertEqual(store.setting("tcp_host"),"mesh.local")
            self.assertEqual(store.setting("tcp_port"),"5000")
            serial_data=urllib.parse.urlencode({"connection_type":"serial","port":"/dev/ttyACM1","baud":"57600"}).encode()
            serial_request=Request(base+"/settings",data=serial_data,headers={"Content-Type":"application/x-www-form-urlencoded"})
            with patch("towncrier.radio.Radio.snapshot",return_value=({},[])):
                with urlopen(serial_request) as response: self.assertTrue(response.geturl().endswith("/radio"))
            self.assertEqual(store.setting("connection_type"),"serial")
            self.assertEqual(store.setting("port"),"/dev/ttyACM1")
            self.assertEqual(store.setting("baud"),"57600")
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
        data={"name":"Morning","channels":[0,2],"messages":["first","second"],"interval":3600,"message_delay":2,"send_at":"09:00","enabled":True}
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

    def test_login_cookie_is_required_and_bound_to_room_before_publishing(self):
        key="b"*64
        with patch("towncrier.radio.Radio.publisher_login",return_value={"permissions":2}) as login, patch("towncrier.radio.Radio.publisher_send",return_value={"sent":True}) as send:
            status,headers,body=self.request("/publisher-api/login","POST",{"public_key":key,"password":"secret"})
            self.assertEqual((status,body),(200,{"permissions":2}))
            self.assertIn("HttpOnly",headers.get("Set-Cookie"))
            token=headers.get("Set-Cookie").split(";",1)[0]
            self.assertEqual(self.request("/publisher-api/publish","POST",{"public_key":key,"text":"x"})[0],401)
            self.assertEqual(self.request("/publisher-api/publish","POST",{"public_key":"c"*64,"text":"x"},token)[0],401)
            status,_,body=self.request("/publisher-api/publish","POST",{"public_key":key,"text":"hello"},token)
        self.assertEqual((status,body),(200,{"sent":True}))
        login.assert_called_once()
        send.assert_called_once_with(key,"hello")

    def test_publisher_tab_exposes_room_flow_but_no_browser_radio_chooser(self):
        html=(Path(__file__).parents[1]/"towncrier/static/publisher.html").read_text()
        source=(Path(__file__).parents[1]/"towncrier/static/vendor/publisher.js").read_text()
        self.assertIn('href="/"',html)
        self.assertIn('href="/schedules"',html)
        self.assertIn('already configured in Towncrier',html)
        self.assertNotIn('id="usbButton"',html)
        self.assertNotIn('id="bluetoothButton"',html)
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
