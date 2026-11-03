import sqlite3, threading, time
MIN_INTERVAL=1800
MAX_MESSAGE=500
class Store:
 def __init__(self,path):
  self.path=str(path); self.lock=threading.RLock()
  with self.connect() as db:
   db.executescript("""CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL); CREATE TABLE IF NOT EXISTS schedules(id INTEGER PRIMARY KEY,name TEXT NOT NULL,channel INTEGER NOT NULL CHECK(channel BETWEEN 0 AND 255),message TEXT NOT NULL CHECK(length(message) BETWEEN 1 AND 500),interval INTEGER NOT NULL CHECK(interval>=60),enabled INTEGER NOT NULL DEFAULT 1,next_send REAL NOT NULL,last_send REAL,last_error TEXT,status TEXT);""")
   db.execute("UPDATE schedules SET interval=? WHERE interval<?",(MIN_INTERVAL,MIN_INTERVAL))
 def connect(self):
  db=sqlite3.connect(self.path,timeout=10); db.row_factory=sqlite3.Row; return db
 def setting(self,key,default=""):
  with self.lock,self.connect() as db:
   row=db.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone(); return row[0] if row else default
 def set_setting(self,key,value):
  with self.lock,self.connect() as db: db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,str(value)))
 def schedules(self):
  with self.lock,self.connect() as db: return [dict(r) for r in db.execute("SELECT * FROM schedules ORDER BY id")]
 def get(self,ident):
  with self.lock,self.connect() as db:
   r=db.execute("SELECT * FROM schedules WHERE id=?",(ident,)).fetchone(); return dict(r) if r else None
 def save(self,data,ident=None):
  name=str(data["name"]).strip()[:80]; channel=int(data["channel"]); interval=int(data["interval"]); message=str(data["message"])
  if not name or not message.strip() or not 0<=channel<=255 or interval<MIN_INTERVAL or interval%60 or not 1<=len(message)<=MAX_MESSAGE: raise ValueError("name, channel 0-255, non-empty message 1-500 chars, and interval of at least 30 minutes required")
  enabled=1 if data.get("enabled",True) else 0
  with self.lock,self.connect() as db:
   if ident: db.execute("UPDATE schedules SET name=?,channel=?,message=?,interval=?,enabled=? WHERE id=?",(name,channel,message,interval,enabled,int(ident)))
   else: db.execute("INSERT INTO schedules(name,channel,message,interval,enabled,next_send,status) VALUES(?,?,?,?,?,?,?)",(name,channel,message,interval,enabled,time.time(),"waiting"))
 def delete(self,ident):
  with self.lock,self.connect() as db: db.execute("DELETE FROM schedules WHERE id=?",(int(ident),))
 def toggle(self,ident):
  with self.lock,self.connect() as db: db.execute("UPDATE schedules SET enabled=1-enabled WHERE id=?",(int(ident),))
 def due(self,now=None):
  with self.lock,self.connect() as db: return [dict(r) for r in db.execute("SELECT * FROM schedules WHERE enabled=1 AND next_send<=? ORDER BY next_send",(now or time.time(),))]
 def sent(self,ident,when,interval):
  with self.lock,self.connect() as db: db.execute("UPDATE schedules SET next_send=?,last_send=?,last_error=NULL,status=? WHERE id=?",(when+interval,when,"sent",int(ident)))
 def failed(self,ident,error):
  with self.lock,self.connect() as db: db.execute("UPDATE schedules SET last_error=?,status=? WHERE id=?",(str(error)[:500],"error",int(ident)))
