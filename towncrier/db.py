import sqlite3, threading, time
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
MIN_INTERVAL=1800
MAX_MESSAGE=500
class Store:
 def __init__(self,path):
  self.path=str(path); self.lock=threading.RLock()
  with self.connect() as db:
   db.executescript("""CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL); CREATE TABLE IF NOT EXISTS saved_lists(name TEXT PRIMARY KEY,created_at REAL NOT NULL,updated_at REAL NOT NULL); CREATE TABLE IF NOT EXISTS saved_list_messages(list_name TEXT NOT NULL REFERENCES saved_lists(name) ON DELETE CASCADE,position INTEGER NOT NULL,message TEXT NOT NULL CHECK(length(message) BETWEEN 1 AND 500),PRIMARY KEY(list_name,position)); CREATE TABLE IF NOT EXISTS schedules(id INTEGER PRIMARY KEY,name TEXT NOT NULL,channel INTEGER NOT NULL CHECK(channel BETWEEN 0 AND 255),message TEXT NOT NULL CHECK(length(message) BETWEEN 1 AND 500),interval INTEGER NOT NULL CHECK(interval>=60),enabled INTEGER NOT NULL DEFAULT 1,next_send REAL NOT NULL,last_send REAL,last_error TEXT,status TEXT,message_delay INTEGER NOT NULL DEFAULT 3,send_limit INTEGER NOT NULL DEFAULT 0,send_count INTEGER NOT NULL DEFAULT 0); CREATE TABLE IF NOT EXISTS schedule_messages(id INTEGER PRIMARY KEY,schedule_id INTEGER NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,position INTEGER NOT NULL,message TEXT NOT NULL CHECK(length(message) BETWEEN 1 AND 500),UNIQUE(schedule_id,position)); CREATE TABLE IF NOT EXISTS schedule_channels(id INTEGER PRIMARY KEY,schedule_id INTEGER NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,position INTEGER NOT NULL,channel INTEGER NOT NULL CHECK(channel BETWEEN 0 AND 255),UNIQUE(schedule_id,position)); CREATE TABLE IF NOT EXISTS schedule_post_times(id INTEGER PRIMARY KEY,schedule_id INTEGER NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,position INTEGER NOT NULL,post_time TEXT NOT NULL,UNIQUE(schedule_id,position)); CREATE TABLE IF NOT EXISTS bot_messages(id INTEGER PRIMARY KEY,received_at REAL NOT NULL,channel INTEGER NOT NULL,text TEXT NOT NULL,triggered INTEGER NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT ''); CREATE TABLE IF NOT EXISTS bot_entries(id INTEGER PRIMARY KEY,channel INTEGER NOT NULL CHECK(channel BETWEEN 0 AND 255),trigger_text TEXT NOT NULL,page_name TEXT NOT NULL,message_delay INTEGER NOT NULL DEFAULT 3,enabled INTEGER NOT NULL DEFAULT 1,created_at REAL NOT NULL); INSERT INTO schedule_messages(schedule_id,position,message) SELECT id,0,message FROM schedules WHERE NOT EXISTS (SELECT 1 FROM schedule_messages WHERE schedule_id=schedules.id); INSERT INTO schedule_channels(schedule_id,position,channel) SELECT id,0,channel FROM schedules WHERE NOT EXISTS (SELECT 1 FROM schedule_channels WHERE schedule_id=schedules.id);""")
   try: db.execute("ALTER TABLE schedules ADD COLUMN message_delay INTEGER NOT NULL DEFAULT 3")
   except sqlite3.OperationalError: pass
   try: db.execute("ALTER TABLE schedules ADD COLUMN send_at TEXT NOT NULL DEFAULT ''")
   except sqlite3.OperationalError: pass
   try: db.execute("ALTER TABLE schedules ADD COLUMN send_limit INTEGER NOT NULL DEFAULT 0")
   except sqlite3.OperationalError: pass
   try: db.execute("ALTER TABLE schedules ADD COLUMN send_count INTEGER NOT NULL DEFAULT 0")
   except sqlite3.OperationalError: pass
   if not any(row[1]=="reply_status" for row in db.execute("PRAGMA table_info(bot_messages)")):
    # Old trigger rows have no known outcome; don't infer that a reply was sent.
    db.execute("ALTER TABLE bot_messages ADD COLUMN reply_status TEXT NOT NULL DEFAULT ''")
   if not any(row[1]=="entry_id" for row in db.execute("PRAGMA table_info(bot_messages)")):
    db.execute("ALTER TABLE bot_messages ADD COLUMN entry_id INTEGER")
   db.execute("UPDATE schedules SET interval=? WHERE interval<?",(MIN_INTERVAL,MIN_INTERVAL))
   db.execute("INSERT INTO schedule_post_times(schedule_id,position,post_time) SELECT id,0,send_at FROM schedules WHERE send_at!='' AND NOT EXISTS (SELECT 1 FROM schedule_post_times WHERE schedule_id=schedules.id)")
   # One-time migration: fold the old single bot_* settings into a bot_entries row.
   if db.execute("SELECT 1 FROM settings WHERE key='bot_entries_migrated'").fetchone() is None:
    legacy=dict(db.execute("SELECT key,value FROM settings WHERE key IN ('bot_channel','bot_trigger','bot_page_name','bot_message_delay','bot_enabled')"))
    trigger=str(legacy.get("bot_trigger","")).strip(); page_name=str(legacy.get("bot_page_name","")).strip()
    if trigger and page_name:
     try: channel=int(legacy.get("bot_channel","-1"))
     except ValueError: channel=-1
     try: delay=max(0,min(int(legacy.get("bot_message_delay","3")),3600))
     except ValueError: delay=3
     if 0<=channel<=255:
      db.execute("INSERT INTO bot_entries(channel,trigger_text,page_name,message_delay,enabled,created_at) VALUES(?,?,?,?,?,?)",(channel,trigger,page_name,delay,1 if legacy.get("bot_enabled","0")=="1" else 0,time.time()))
    db.execute("INSERT INTO settings(key,value) VALUES('bot_entries_migrated','1') ON CONFLICT(key) DO NOTHING")
 @contextmanager
 def connect(self):
  # SQLite's transaction context commits or rolls back, but does not close.
  with closing(sqlite3.connect(self.path,timeout=10)) as db, db:
   db.row_factory=sqlite3.Row; db.execute("PRAGMA foreign_keys=ON")
   yield db
 def setting(self,key,default=""):
  with self.lock,self.connect() as db:
   row=db.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone(); return row[0] if row else default
 def set_setting(self,key,value):
  self.set_settings(**{key:value})
 def set_settings(self,**values):
  with self.lock,self.connect() as db:
   db.executemany("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",[(key,str(value)) for key,value in values.items()])
 def settings(self,defaults):
  with self.lock,self.connect() as db:
   return {**defaults,**{row[0]:row[1] for row in db.execute("SELECT key,value FROM settings") if row[0] in defaults}}
 def saved_lists(self):
  with self.lock,self.connect() as db:
   return [{"name":row[0],"messages":[r[0] for r in db.execute("SELECT message FROM saved_list_messages WHERE list_name=? ORDER BY position",(row[0],))]} for row in db.execute("SELECT name FROM saved_lists ORDER BY name COLLATE NOCASE")]
 def record_bot_message(self,channel,text,triggered=False,error="",entry_id=None):
  with self.lock,self.connect() as db:
   row=db.execute("INSERT INTO bot_messages(received_at,channel,text,triggered,error,reply_status,entry_id) VALUES(?,?,?,?,?,?,?)",(time.time(),int(channel),str(text),1 if triggered else 0,str(error)[:500],"pending" if triggered else "",entry_id))
   db.execute("DELETE FROM bot_messages WHERE id NOT IN (SELECT id FROM bot_messages ORDER BY id DESC LIMIT 500)")
   return row.lastrowid
 def finish_bot_reply(self,message_id,status,error=""):
  with self.lock,self.connect() as db:
   db.execute("UPDATE bot_messages SET reply_status=?,error=? WHERE id=? AND reply_status='pending'",(status,str(error)[:500],message_id))
 def interrupt_pending_bot_replies(self):
  with self.lock,self.connect() as db:
   db.execute("UPDATE bot_messages SET reply_status='interrupted',error=? WHERE reply_status='pending'",("Listener restarted before the reply finished; not retried.",))
 def bot_messages(self,channel=None,limit=100):
  with self.lock,self.connect() as db:
   query="SELECT received_at,channel,text,triggered,error,reply_status,entry_id FROM bot_messages"; args=[]
   if channel is not None: query+=" WHERE channel=?"; args.append(int(channel))
   query+=" ORDER BY id DESC LIMIT ?"; args.append(int(limit))
   return [{"time":row[0],"channel":row[1],"text":row[2],"triggered":bool(row[3]),"error":row[4],"reply_status":row[5],"entry_id":row[6]} for row in db.execute(query,args)]
 def _bot_entry_row(self,row):
  item=dict(row); item["trigger"]=item.pop("trigger_text"); return item
 def _legacy_bot_entry(self,db):
  # Callers that still poke the old single bot_* settings (pre-upgrade scripts,
  # older tests) get a synthetic entry computed live, never persisted, so it
  # can't duplicate a real row once one exists.
  legacy=dict(db.execute("SELECT key,value FROM settings WHERE key IN ('bot_channel','bot_trigger','bot_page_name','bot_message_delay','bot_enabled')"))
  trigger=str(legacy.get("bot_trigger","")).strip(); page_name=str(legacy.get("bot_page_name","")).strip()
  if not trigger or not page_name: return None
  try: channel=int(legacy.get("bot_channel","-1"))
  except ValueError: channel=-1
  if not 0<=channel<=255: return None
  try: delay=max(0,min(int(legacy.get("bot_message_delay","3")),3600))
  except ValueError: delay=3
  return {"id":0,"channel":channel,"trigger":trigger,"page_name":page_name,"message_delay":delay,"enabled":1 if legacy.get("bot_enabled","0")=="1" else 0,"created_at":0.0}
 def real_bot_entries(self):
  with self.lock,self.connect() as db:
   return [self._bot_entry_row(row) for row in db.execute("SELECT * FROM bot_entries ORDER BY id")]
 def bot_entries(self):
  with self.lock,self.connect() as db:
   rows=[self._bot_entry_row(row) for row in db.execute("SELECT * FROM bot_entries ORDER BY id")]
   if rows: return rows
   legacy=self._legacy_bot_entry(db)
   return [legacy] if legacy else []
 def active_bot_entries(self):
  return [e for e in self.bot_entries() if e["enabled"]]
 def get_bot_entry(self,ident):
  with self.lock,self.connect() as db:
   row=db.execute("SELECT * FROM bot_entries WHERE id=?",(int(ident),)).fetchone(); return self._bot_entry_row(row) if row else None
 def save_bot_entry(self,data,ident=None):
  try: channel=int(data.get("channel",-1))
  except (TypeError,ValueError): raise ValueError("channel must be between 0 and 255")
  trigger=str(data.get("trigger","")).strip(); page_name=str(data.get("page_name","")).strip()
  try: message_delay=int(data.get("message_delay",3))
  except (TypeError,ValueError): raise ValueError("message delay must be a whole number of seconds")
  if not 0<=channel<=255 or not trigger or len(trigger)>32 or not page_name or not 0<=message_delay<=3600:
   raise ValueError("channel 0-255, a trigger word (1-32 characters), a saved page, and a message delay 0-3600 seconds are required")
  if not any(item["name"]==page_name for item in self.saved_lists()): raise ValueError(f"saved page '{page_name}' does not exist")
  with self.lock,self.connect() as db:
   if ident:
    ident=int(ident); existing=db.execute("SELECT * FROM bot_entries WHERE id=?",(ident,)).fetchone()
    if not existing: raise ValueError("bot entry not found")
    enabled=existing["enabled"]
    if enabled and db.execute("SELECT id FROM bot_entries WHERE enabled=1 AND channel=? AND lower(trigger_text)=lower(?) AND id!=?",(channel,trigger,ident)).fetchone():
     raise ValueError(f"an active entry already listens for '{trigger}' on channel {channel}")
    db.execute("UPDATE bot_entries SET channel=?,trigger_text=?,page_name=?,message_delay=? WHERE id=?",(channel,trigger,page_name,message_delay,ident))
   else:
    enabled=1 if data.get("enabled",True) else 0
    if enabled and db.execute("SELECT id FROM bot_entries WHERE enabled=1 AND channel=? AND lower(trigger_text)=lower(?)",(channel,trigger)).fetchone():
     raise ValueError(f"an active entry already listens for '{trigger}' on channel {channel}")
    row=db.execute("INSERT INTO bot_entries(channel,trigger_text,page_name,message_delay,enabled,created_at) VALUES(?,?,?,?,?,?)",(channel,trigger,page_name,message_delay,enabled,time.time()))
    ident=row.lastrowid
  return ident
 def toggle_bot_entry(self,ident):
  with self.lock,self.connect() as db:
   row=db.execute("SELECT * FROM bot_entries WHERE id=?",(int(ident),)).fetchone()
   if not row: raise ValueError("bot entry not found")
   if not row["enabled"] and db.execute("SELECT id FROM bot_entries WHERE enabled=1 AND channel=? AND lower(trigger_text)=lower(?) AND id!=?",(row["channel"],row["trigger_text"],row["id"])).fetchone():
    raise ValueError(f"an active entry already listens for '{row['trigger_text']}' on channel {row['channel']}")
   db.execute("UPDATE bot_entries SET enabled=1-enabled WHERE id=?",(row["id"],))
 def delete_bot_entry(self,ident):
  with self.lock,self.connect() as db: db.execute("DELETE FROM bot_entries WHERE id=?",(int(ident),))
 def save_list(self,name,messages):
  name=str(name).strip()[:80]; messages=[str(m) for m in messages if str(m).strip()]
  if not name: raise ValueError("list name is required")
  if not messages: raise ValueError("list must contain at least one message")
  if any(len(m)>500 for m in messages): raise ValueError("messages must be 500 characters or fewer")
  now=time.time()
  with self.lock,self.connect() as db:
   db.execute("INSERT INTO saved_lists(name,created_at,updated_at) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET updated_at=excluded.updated_at",(name,now,now))
   db.execute("DELETE FROM saved_list_messages WHERE list_name=?",(name,))
   db.executemany("INSERT INTO saved_list_messages(list_name,position,message) VALUES(?,?,?)",[(name,i,m) for i,m in enumerate(messages)])
  return name
 def delete_list(self,name):
  with self.lock,self.connect() as db: db.execute("DELETE FROM saved_lists WHERE name=?",(str(name),))
 def _messages(self,db,ident):
  return [r[0] for r in db.execute("SELECT message FROM schedule_messages WHERE schedule_id=? ORDER BY position",(ident,))]
 def _channels(self,db,ident):
  return [r[0] for r in db.execute("SELECT channel FROM schedule_channels WHERE schedule_id=? ORDER BY position",(ident,))]
 def _post_times(self,db,ident):
  return [row[0] for row in db.execute("SELECT post_time FROM schedule_post_times WHERE schedule_id=? ORDER BY position",(ident,))]
 def schedules(self):
  with self.lock,self.connect() as db:
   rows=[]
   for row in db.execute("SELECT * FROM schedules ORDER BY id"):
    item=dict(row); item["messages"]=self._messages(db,row["id"]); item["channels"]=self._channels(db,row["id"]); item["post_times"]=self._post_times(db,row["id"]); rows.append(item)
   return rows
 def get(self,ident):
  with self.lock,self.connect() as db:
   r=db.execute("SELECT * FROM schedules WHERE id=?",(ident,)).fetchone()
   if not r: return None
   item=dict(r); item["messages"]=self._messages(db,r["id"]); item["channels"]=self._channels(db,r["id"]); item["post_times"]=self._post_times(db,r["id"]); return item
 def save(self,data,ident=None):
  name=str(data["name"]).strip()[:80]; channel=int(data.get("channel",0)); interval=int(data.get("interval",MIN_INTERVAL)); message_delay=int(data.get("message_delay",3)); send_limit=max(0,int(data.get("send_limit",0)))
  channels=data.get("channels",data.get("channel",[])); channels=[int(c) for c in ([channels] if isinstance(channels,(str,int)) else channels)]
  messages=data.get("messages",data.get("message",[])); messages=[str(m) for m in ([messages] if isinstance(messages,str) else messages) if str(m).strip()]
  send_at=str(data.get("send_at", ""))
  post_times=[str(value) for value in (data.get("post_times") if "post_times" in data else ([send_at] if send_at else []))]
  if len(post_times)!=len(set(post_times)): raise ValueError("daily post times must be unique")
  post_times=sorted(post_times)
  legacy_interval=not post_times
  for index, value in enumerate(post_times):
   try: datetime.strptime(value,"%H:%M")
   except ValueError: raise ValueError("post times must use 24-hour HH:MM format")
   if index and value==post_times[index-1]: raise ValueError("daily post times must be unique")
  if not name or not channels or not all(0<=c<=255 for c in channels) or not messages or not all(1<=len(m)<=MAX_MESSAGE for m in messages) or (not post_times and (interval<MIN_INTERVAL or interval%60)) or not 0<=message_delay<=3600: raise ValueError("name, channels 0-255, messages 1-500 chars, valid daily post times, and message delay 0-3600 seconds required")
  channel=channels[0]; enabled=1 if data.get("enabled",True) else 0; existing=None
  with self.lock,self.connect() as db:
   if ident:
    ident=int(ident); existing=self.get(ident)
    if not existing: raise ValueError("schedule not found")
    # Only the dedicated Pause/Enable action changes an existing schedule’s state.
    db.execute("UPDATE schedules SET name=?,channel=?,message=?,interval=?,message_delay=?,send_limit=? WHERE id=?",(name,channel,messages[0],interval,message_delay,send_limit,ident))
   else:
    db.execute("INSERT INTO schedules(name,channel,message,interval,enabled,next_send,status,message_delay,send_at,send_limit,send_count) VALUES(?,?,?,?,?,?,?,?,?,?,0)",(name,channel,messages[0],interval,enabled,self._next_send(send_at,interval,post_times),"waiting",message_delay,post_times[0] if post_times else send_at,send_limit)); ident=db.execute("SELECT last_insert_rowid()").fetchone()[0]
   db.execute("DELETE FROM schedule_post_times WHERE schedule_id=?",(ident,)); db.executemany("INSERT INTO schedule_post_times(schedule_id,position,post_time) VALUES(?,?,?)",[(ident,i,value) for i,value in enumerate(post_times)])
   if existing is None:
    db.execute("UPDATE schedules SET send_at=? WHERE id=?",(post_times[0] if post_times else send_at,ident))
   db.execute("DELETE FROM schedule_messages WHERE schedule_id=?",(ident,)); db.executemany("INSERT INTO schedule_messages(schedule_id,position,message) VALUES(?,?,?)",[(ident,i,message) for i,message in enumerate(messages)])
   db.execute("DELETE FROM schedule_channels WHERE schedule_id=?",(ident,)); db.executemany("INSERT INTO schedule_channels(schedule_id,position,channel) VALUES(?,?,?)",[(ident,i,c) for i,c in enumerate(channels)])
  return ident
 def _next_send(self,send_at,interval,post_times=None,now=None):
  now=now or datetime.now().astimezone()
  times=post_times or ([send_at] if send_at else [])
  if not times: return (now+timedelta(seconds=interval)).timestamp()
  for value in times:
   hour,minute=map(int,value.split(":")); candidate=now.replace(hour=hour,minute=minute,second=0,microsecond=0)
   if candidate>now: return candidate.timestamp()
  tomorrow=now+timedelta(days=1); hour,minute=map(int,times[0].split(":"))
  return tomorrow.replace(hour=hour,minute=minute,second=0,microsecond=0).timestamp()
 def _schedule_next(self,ident,when,interval):
  with self.lock,self.connect() as db:
   times=self._post_times(db,ident)
   next_send=self._next_send("",interval,times,datetime.fromtimestamp(when).astimezone())
   db.execute("UPDATE schedules SET next_send=?,last_send=?,last_error=NULL,status=?,send_count=send_count+1,enabled=CASE WHEN send_limit>0 AND send_count+1>=send_limit THEN 0 ELSE enabled END WHERE id=?",(next_send,when,"sent",int(ident)))

 def delete(self,ident):
  with self.lock,self.connect() as db: db.execute("DELETE FROM schedules WHERE id=?",(int(ident),))
 def toggle(self,ident):
  with self.lock,self.connect() as db: db.execute("UPDATE schedules SET enabled=1-enabled WHERE id=?",(int(ident),))
 def due(self,now=None):
  with self.lock,self.connect() as db:
   rows=[]
   for row in db.execute("SELECT * FROM schedules WHERE enabled=1 AND next_send<=? ORDER BY next_send",(now or time.time(),)):
    item=dict(row); item["messages"]=self._messages(db,row["id"]); item["channels"]=self._channels(db,row["id"]); item["post_times"]=self._post_times(db,row["id"]); rows.append(item)
   return rows
 def sent(self,ident,when,interval):
  row=self.get(ident)
  if row and row["post_times"]:
   return self._schedule_next(ident,when,interval)
  with self.lock,self.connect() as db: db.execute("UPDATE schedules SET next_send=?,last_send=?,last_error=NULL,status=?,send_count=send_count+1,enabled=CASE WHEN send_limit>0 AND send_count+1>=send_limit THEN 0 ELSE enabled END WHERE id=?",(when+interval,when,"sent",int(ident)))
 def failed(self,ident,error):
  # Keep a failed radio from tying up every scheduler pass.
  with self.lock,self.connect() as db: db.execute("UPDATE schedules SET last_error=?,status=?,next_send=MAX(next_send,?) WHERE id=?",(str(error)[:500],"error",time.time()+60,int(ident)))
