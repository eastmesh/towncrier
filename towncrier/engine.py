import asyncio,threading,time
from collections import deque
from .radio import Radio
TARGET_CHARS=120
MAX_CHUNK=130
MESSAGE_DELAY=3
AMBIGUOUS_RESPONSE={"no_event_received","timeout"}
BOT_FETCH_ATTEMPTS=3

_bot_messages=deque(maxlen=100)
_bot_messages_lock=threading.Lock()

def recent_bot_messages(channel=None):
 with _bot_messages_lock:
  rows=list(_bot_messages)
 if channel is not None: rows=[row for row in rows if row["channel"]==channel]
 return rows

def record_bot_message(channel,text,triggered=False,error=""):
 with _bot_messages_lock:
  _bot_messages.append({"time":time.time(),"channel":channel,"text":text,"triggered":triggered,"error":error})
 set_bot_status(last_message={"time":time.time(),"channel":channel,"text":text})

_bot_status={"state":"idle","channels":[],"last_message":None,"error":""}
_bot_status_lock=threading.Lock()

def bot_listener_status():
 with _bot_status_lock: return dict(_bot_status)

def set_bot_status(**values):
 with _bot_status_lock: _bot_status.update(values)

def _utf8_prefix(text, limit):
 # Slice at a character boundary; a radio limit counts encoded bytes.
 return text.encode("utf-8")[:limit].decode("utf-8", errors="ignore")

def split_message(message):
 # A textbox that fits is already one radio message; preserve its formatting.
 if len(message.encode("utf-8"))<=MAX_CHUNK: return [message] if message else []
 if "\n" not in message and "\r" not in message:
  words=message.split(); chunks=[]; current=""
  for word in words:
   while len(word.encode("utf-8"))>MAX_CHUNK:
    if current: chunks.append(current); current=""
    part=_utf8_prefix(word,MAX_CHUNK); chunks.append(part); word=word[len(part):]
   candidate=word if not current else current+" "+word
   if current and (len(candidate.encode("utf-8"))>MAX_CHUNK or len(current.encode("utf-8"))>=TARGET_CHARS):
    chunks.append(current); current=word
   else: current=candidate
  if current: chunks.append(current)
  return chunks
 chunks=[]; current=""
 for line in message.splitlines(keepends=True):
  while line:
   room=MAX_CHUNK-len(current.encode("utf-8"))
   if len(line.encode("utf-8"))<=room:
    current+=line; line=""
   else:
    if current: chunks.append(current); current=""; room=MAX_CHUNK
    part=_utf8_prefix(line,room); chunks.append(part); line=line[len(part):]
 if current: chunks.append(current)
 return chunks

def send_message(radio,channel,message,delay=MESSAGE_DELAY,stop_event=None):
 channels=[channel] if isinstance(channel,int) else channel
 messages=[message] if isinstance(message,str) else message
 for target in channels:
  for index,text in enumerate(messages):
   for chunk in split_message(text):
    if stop_event is not None and stop_event.is_set(): raise ConnectionError("Scheduler stopped")
    radio.send(target,chunk)
   if index+1<len(messages):
    if stop_event is None: time.sleep(delay)
    elif stop_event.wait(delay): raise ConnectionError("Scheduler stopped")

class BotListener(threading.Thread):
 daemon=True
 def __init__(self,store,stop_event):
  super().__init__(); self.store,self.stop_event=store,stop_event; self.seen_messages=set(); self.initial_drained=False; self.owner=getattr(store,"radio_owner",None)
 def _load_entries(self):
  return self.store.active_bot_entries()
 def _snapshot(self,entries):
  return tuple(sorted((e["id"],e["channel"],e["trigger"],e["page_name"],e["message_delay"]) for e in entries))
 async def _command(self, mc, command_lock, name, *args):
  if self.owner is not None:
   return await self.owner.run_bot_command(mc,name,*args)
  async with command_lock: return await getattr(mc.commands,name)(*args)
 async def _post_page(self,mc,event_type,channel,page_name,message_delay=3,command_lock=None):
  page=next((item for item in self.store.saved_lists() if item["name"]==page_name),None)
  if not page or not page["messages"]: raise RuntimeError(f"Saved reply page '{page_name}' is missing or empty")
  command_lock=command_lock or asyncio.Lock()
  unconfirmed=False
  for index,message in enumerate(page["messages"]):
   for chunk in split_message(message):
    try:
     result=await self._command(mc,command_lock,"send_chan_msg",channel,chunk)
    except TimeoutError:
     unconfirmed=True
     continue
    result_type=getattr(result,"type",None); payload=getattr(result,"payload",None) or {}; reason=payload.get("reason") if isinstance(payload,dict) else None
    if event_type and result_type==event_type.ERROR:
     if reason in AMBIGUOUS_RESPONSE:
      # A missing acknowledgement does not mean the radio didn't send. Don't retry.
      unconfirmed=True
      continue
     raise RuntimeError(payload)
    if event_type and result_type!=getattr(event_type,"OK",object()): unconfirmed=True
   if index+1<len(page["messages"]) and message_delay: await asyncio.sleep(message_delay)
  if unconfirmed: return "unconfirmed","Radio confirmation unavailable for one or more reply messages."
  return "sent",""
 async def _fetch_queued_messages(self,mc,event_type,command_lock=None):
  command_lock=command_lock or asyncio.Lock()
  attempts=0
  while True:
   try:
    result=await self._command(mc,command_lock,"get_msg")
   except TimeoutError:
    result=None
   result_type=getattr(result,"type",None); payload=getattr(result,"payload",None) or {}
   if result_type==event_type.NO_MORE_MSGS: return
   if result_type in (getattr(event_type,"CHANNEL_MSG_RECV",object()),getattr(event_type,"CONTACT_MSG_RECV",object())):
    attempts=0
    await asyncio.sleep(0)
    continue
   reason=payload.get("reason") if isinstance(payload,dict) else None
   if result_type==event_type.ERROR and reason=="no_more_msgs": return
   if result is not None and not (result_type==event_type.ERROR and reason in AMBIGUOUS_RESPONSE):
    raise ConnectionError(f"radio fetch failed: {payload or result_type}")
   # A timeout is not evidence that the radio queue is empty. Retry without
   # waiting for a new MESSAGES_WAITING event, then let the session reconnect.
   attempts+=1
   if attempts>=BOT_FETCH_ATTEMPTS: raise ConnectionError("radio fetch timed out repeatedly")
   await asyncio.sleep(.25)
 async def _run_async(self):
  from meshcore import EventType
  entries=self._load_entries()
  if not entries:
   set_bot_status(state="disabled",channels=[],error="No active bot entries configured")
   return
  channels={e["channel"] for e in entries}
  config=self._snapshot(entries)
  if self.owner is not None:
   async with self.owner.connection() as mc:
    await mc.stop_auto_message_fetching()
  else:
   radio=Radio.from_store(self.store); mc=await radio._connect()
  subscription=waiting_subscription=None
  pending_replies=asyncio.Queue(); fetch_requested=asyncio.Event(); command_lock=asyncio.Lock()
  tasks=[]
  accept_messages=self.initial_drained
  def on_message(event):
   if not accept_messages: return
   try:
    payload=getattr(event,"payload",None) or {}; text=str(payload.get("text", "")); incoming_channel=payload.get("channel_idx")
    if incoming_channel not in channels: return
    key=(payload.get("sender_timestamp"),text,incoming_channel)
    if key in self.seen_messages: return
    matched_text=text.split(":",1)[1].lstrip() if ":" in text else text
    matched_text=matched_text.strip().casefold()
    entry=next((e for e in entries if e["channel"]==incoming_channel and matched_text==e["trigger"].casefold()),None)
    message_id=self.store.record_bot_message(incoming_channel,text,entry is not None,entry_id=entry["id"] if entry else None)
    self.seen_messages.add(key)
    record_bot_message(incoming_channel,text,entry is not None)
    if entry: pending_replies.put_nowait((message_id,entry))
   except Exception as exc:
    set_bot_status(state="listening",channels=sorted(channels),error=f"message handling failed: {exc}")
  def on_messages_waiting(_event): fetch_requested.set()
  async def post_replies():
   while True:
    message_id,entry=await pending_replies.get()
    try:
     status,error=await self._post_page(mc,EventType,entry["channel"],entry["page_name"],entry["message_delay"],command_lock)
    except asyncio.CancelledError:
     self.store.finish_bot_reply(message_id,"interrupted","Listener stopped before the reply finished; not retried.")
     raise
    except Exception as exc:
     status,error="failed",str(exc)
    self.store.finish_bot_reply(message_id,status,error)
    # Give the listener a chance to stop before starting another queued page.
    await asyncio.sleep(0)
  async def fetch_messages():
   nonlocal accept_messages
   if not accept_messages:
    set_bot_status(state="draining",channels=sorted(channels),error="Clearing queued radio messages")
    await self._fetch_queued_messages(mc,EventType,command_lock)
    dispatcher_queue=getattr(getattr(mc,"dispatcher",None),"queue",None)
    if dispatcher_queue is not None: await dispatcher_queue.join()
    accept_messages=self.initial_drained=True
   else:
    # Recover queued traffic after reconnect, even if its notification was lost.
    fetch_requested.set()
   set_bot_status(state="listening",channels=sorted(channels),error="")
   while not self.stop_event.is_set():
    await fetch_requested.wait()
    fetch_requested.clear()
    await self._fetch_queued_messages(mc,EventType,command_lock)
  try:
   if self.owner is None: await mc.stop_auto_message_fetching()
   waiting_subscription=mc.subscribe(EventType.MESSAGES_WAITING,on_messages_waiting)
   subscription=mc.subscribe(EventType.CHANNEL_MSG_RECV,on_message)
   # Keep a single FIFO reply worker and a single fetch worker. Serialize only
   # radio commands, not the delay between reply messages.
   tasks=[asyncio.create_task(fetch_messages()),asyncio.create_task(post_replies())]
   while not self.stop_event.is_set():
    if self._snapshot(self._load_entries())!=config: break
    if not mc.is_connected or (self.owner is not None and mc is not self.owner.device): raise ConnectionError("radio disconnected")
    done,_=await asyncio.wait(tasks,timeout=.25,return_when=asyncio.FIRST_COMPLETED)
    if done:
     for task in done: task.result()
     break
  except OSError:
   if self.owner is not None: await self.owner.drop_if_current(mc)
   raise
  finally:
   accept_messages=False
   for item in (subscription,waiting_subscription):
    if item is not None:
     try: mc.unsubscribe(item)
     except Exception: pass
   try:
    for task in tasks: task.cancel()
    await asyncio.gather(*tasks,return_exceptions=True)
    while not pending_replies.empty():
     message_id,_entry=pending_replies.get_nowait()
     self.store.finish_bot_reply(message_id,"interrupted","Listener stopped before the reply started; not retried.")
   finally:
    if self.owner is None: await mc.disconnect()
 def run(self):
  self.store.interrupt_pending_bot_replies()
  while not self.stop_event.is_set():
   entries=self._load_entries()
   if not entries:
    self.initial_drained=False
    set_bot_status(state="disabled",channels=[],error="No active bot entries configured")
    self.stop_event.wait(5); continue
   try:
    if self.owner is not None: self.owner.run(self._run_async)
    else: asyncio.run(self._run_async())
   except Exception as exc:
    set_bot_status(state="reconnecting",channels=sorted({e["channel"] for e in entries}),error=f"radio connection lost; retrying: {exc}"); self.stop_event.wait(2)
class Scheduler(threading.Thread):
 daemon=True
 def __init__(self,store,stop_event): super().__init__(); self.store,self.stop_event=store,stop_event
 def run(self):
  while not self.stop_event.is_set():
   rows=self.store.due()
   for row in rows:
    if self.stop_event.is_set(): break
    try:
     messages=self.store.resolve_messages(row)
     send_message(Radio.from_store(self.store),row["channels"],messages,row["message_delay"],self.stop_event); self.store.sent(row["id"],row["next_send"],row["interval"])
    except Exception as exc: self.store.failed(row["id"],exc)
   self.stop_event.wait(1 if rows else 10)
