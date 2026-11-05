import sqlite3, threading, time
MIN_INTERVAL=1800
MAX_MESSAGE=500
class Store:
 def __init__(self,path):
  self.path=str(path); self.lock=threading.RLock()
  with self.connect() as db:
   db.executescript("""CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL); CREATE TABLE IF NOT EXISTS schedules(id INTEGER PRIMARY KEY,name TEXT NOT NULL,channel INTEGER NOT NULL CHECK(channel BETWEEN 0 AND 255),message TEXT NOT NULL CHECK(length(message) BETWEEN 1 AND 500),interval INTEGER NOT NULL CHECK(interval>=60),enabled INTEGER NOT NULL DEFAULT 1,next_send REAL NOT NULL,last_send REAL,last_error TEXT,status TEXT); CREATE TABLE IF NOT EXISTS schedule_messages(id INTEGER PRIMARY KEY,schedule_id INTEGER NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,position INTEGER NOT NULL,message TEXT NOT NULL CHECK(length(message) BETWEEN 1 AND 500),UNIQUE(schedule_id,position)); CREATE TABLE IF NOT EXISTS schedule_channels(id INTEGER PRIMARY KEY,schedule_id INTEGER NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,position INTEGER NOT NULL,channel INTEGER NOT NULL CHECK(channel BETWEEN 0 AND 255),UNIQUE(schedule_id,position)); INSERT INTO schedule_messages(schedule_id,position,message) SELECT id,0,message FROM schedules WHERE NOT EXISTS (SELECT 1 FROM schedule_messages WHERE schedule_id=schedules.id); INSERT INTO schedule_channels(schedule_id,position,channel) SELECT id,0,channel FROM schedules WHERE NOT EXISTS (SELECT 1 FROM schedule_channels WHERE schedule_id=schedules.id);""")
   db.execute("UPDATE schedules SET interval=? WHERE interval<?",(MIN_INTERVAL,MIN_INTERVAL))
 def connect(self):
  db=sqlite3.connect(self.path,timeout=10); db.row_factory=sqlite3.Row; db.execute("PRAGMA foreign_keys=ON"); return db
 def setting(self,key,default=""):
  with self.lock,self.connect() as db:
   row=db.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone(); return row[0] if row else default
 def set_setting(self,key,value):
  with self.lock,self.connect() as db: db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,str(value)))
 def _messages(self,db,ident):
  return [r[0] for r in db.execute("SELECT message FROM schedule_messages WHERE schedule_id=? ORDER BY position",(ident,))]
 def _channels(self,db,ident):
  return [r[0] for r in db.execute("SELECT channel FROM schedule_channels WHERE schedule_id=? ORDER BY position",(ident,))]
 def schedules(self):
  with self.lock,self.connect() as db:
   rows=[]
   for row in db.execute("SELECT * FROM schedules ORDER BY id"):
    item=dict(row); item["messages"]=self._messages(db,row["id"]); item["channels"]=self._channels(db,row["id"]); rows.append(item)
   return rows
 def get(self,ident):
  with self.lock,self.connect() as db:
   r=db.execute("SELECT * FROM schedules WHERE id=?",(ident,)).fetchone()
   if not r: return None
   item=dict(r); item["messages"]=self._messages(db,r["id"]); item["channels"]=self._channels(db,r["id"]); return item
 def save(self,data,ident=None):
  name=str(data["name"]).strip()[:80]; channel=int(data.get("channel",0)); interval=int(data["interval"])
  channels=data.get("channels",data.get("channel",[])); channels=[int(c) for c in ([channels] if isinstance(channels,(str,int)) else channels)]
  messages=data.get("messages",data.get("message",[])); messages=[str(m) for m in ([messages] if isinstance(messages,str) else messages) if str(m).strip()]
  if not name or not channels or not all(0<=c<=255 for c in channels) or not messages or not all(1<=len(m)<=MAX_MESSAGE for m in messages) or interval<MIN_INTERVAL or interval%60: raise ValueError("name, channels 0-255, non-empty messages 1-500 chars each, and interval of at least 30 minutes required")
  channel=channels[0]; enabled=1 if data.get("enabled",True) else 0
  with self.lock,self.connect() as db:
   if ident:
    ident=int(ident); db.execute("UPDATE schedules SET name=?,channel=?,message=?,interval=?,enabled=? WHERE id=?",(name,channel,messages[0],interval,enabled,ident))
   else:
    db.execute("INSERT INTO schedules(name,channel,message,interval,enabled,next_send,status) VALUES(?,?,?,?,?,?,?)",(name,channel,messages[0],interval,enabled,time.time()+interval,"waiting")); ident=db.execute("SELECT last_insert_rowid()").fetchone()[0]
   db.execute("DELETE FROM schedule_messages WHERE schedule_id=?",(ident,)); db.executemany("INSERT INTO schedule_messages(schedule_id,position,message) VALUES(?,?,?)",[(ident,i,message) for i,message in enumerate(messages)])
   db.execute("DELETE FROM schedule_channels WHERE schedule_id=?",(ident,)); db.executemany("INSERT INTO schedule_channels(schedule_id,position,channel) VALUES(?,?,?)",[(ident,i,c) for i,c in enumerate(channels)])
 def delete(self,ident):
  with self.lock,self.connect() as db: db.execute("DELETE FROM schedules WHERE id=?",(int(ident),))
 def toggle(self,ident):
  with self.lock,self.connect() as db: db.execute("UPDATE schedules SET enabled=1-enabled WHERE id=?",(int(ident),))
 def due(self,now=None):
  with self.lock,self.connect() as db:
   rows=[]
   for row in db.execute("SELECT * FROM schedules WHERE enabled=1 AND next_send<=? ORDER BY next_send",(now or time.time(),)):
    item=dict(row); item["messages"]=self._messages(db,row["id"]); item["channels"]=self._channels(db,row["id"]); rows.append(item)
   return rows
 def sent(self,ident,when,interval):
  with self.lock,self.connect() as db: db.execute("UPDATE schedules SET next_send=?,last_send=?,last_error=NULL,status=? WHERE id=?",(when+interval,when,"sent",int(ident)))
 def failed(self,ident,error):
  with self.lock,self.connect() as db: db.execute("UPDATE schedules SET last_error=?,status=? WHERE id=?",(str(error)[:500],"error",int(ident)))
