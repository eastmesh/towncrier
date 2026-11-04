import argparse, html, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from .db import Store
from .engine import Scheduler, send_message
CSS=""":root{--ink:#222;--surface:#303030;--soft:#343434;--text:#e6eaf0;--muted:#9aa4b2;--green:#36a167;--green-dark:#267342;--deep:#224c36;--blue:#1e3a5f;--border:rgba(255,255,255,.08);--shadow:0 10px 30px rgba(0,0,0,.45)}*{box-sizing:border-box}body{margin:0;background:var(--ink);color:var(--text);font:16px/1.6 Inter,system-ui,-apple-system,"Segoe UI",sans-serif}main{max-width:1200px;margin:0 auto;padding:36px 24px 48px}header{border-bottom:1px solid var(--border);margin-bottom:24px;padding-bottom:20px}h1{font-size:2.4rem;line-height:1.15;margin:0;color:#fff}h1:before{content:'◈';color:var(--green);margin-right:12px}h2{font-size:1.5rem;line-height:1.25;margin-top:0}h3{margin-bottom:8px}.eyebrow{color:var(--green);font-size:.75rem;font-weight:700;letter-spacing:.14em;text-transform:uppercase}.muted,small{color:var(--muted)}.card{background:var(--surface);border:1px solid var(--border);border-radius:14px;box-shadow:var(--shadow);padding:24px;margin:24px 0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px}label{display:flex;flex-direction:column;gap:4px;font-weight:600}input,textarea,select,button{font:inherit}input,textarea,select{background:var(--soft);border:1px solid var(--border);border-radius:10px;color:var(--text);padding:9px 11px}textarea{width:100%;min-height:110px}input:focus,textarea:focus,select:focus{outline:2px solid var(--green);outline-offset:2px}button{background:var(--green-dark);border:0;border-radius:999px;color:#fff;cursor:pointer;padding:9px 15px;font-weight:700}button:hover{background:#287a43}.secondary{background:var(--deep)}.danger{background:#7f2933}.actions{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.inline{display:inline;margin:0;padding:0;border:0;box-shadow:none;background:none}table{width:100%;border-collapse:collapse}th{color:var(--muted);font-size:.75rem;letter-spacing:.1em;text-align:left;text-transform:uppercase}td,th{border-bottom:1px solid var(--border);padding:12px 8px}code{background:var(--soft);border-radius:10px;padding:2px 6px}@media(max-width:700px){main{padding:24px 12px}.card{padding:16px}table{display:block;overflow-x:auto;white-space:nowrap}h1{font-size:2rem}}"""
RADIO_PRESETS={"Australia":(915.800,250,10,5),"Australia (Narrow)":(916.575,62.5,7,7),"Australia (Mid)":(915.075,125,9,5),"Australia: SA, WA":(923.125,62.5,8,8),"Australia: QLD":(923.125,62.5,8,5)}
def esc(v): return html.escape(str(v),quote=True)
def duration_parts(seconds): return int(seconds)//3600,(int(seconds)%3600)//60
def duration_label(seconds):
 h,m=duration_parts(seconds); return (f"{h}h " if h else "")+f"{m}m"
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  path=urlparse(self.path).path
  if path=="/": return self.page()
  if path=="/health": return self.respond("OK","text/plain")
  self.send_error(404)
 def do_POST(self):
  form={k:v[0] for k,v in parse_qs(self.rfile.read(int(self.headers.get("Content-Length",0))).decode(),keep_blank_values=True).items()}; path=urlparse(self.path).path
  try:
   if path=="/settings": self.store.set_setting("port",form.get("port","/dev/ttyUSB0")); self.store.set_setting("baud",form.get("baud","115200"))
   elif path=="/radio-settings":
    from .radio import Radio
    preset=form.get("preset","")
    if preset not in RADIO_PRESETS: raise ValueError("choose a valid Australian MeshCore preset")
    Radio(self.store.setting("port","/dev/ttyUSB0"),self.store.setting("baud","115200")).set_radio(*RADIO_PRESETS[preset])
   elif path=="/channel-save":
    from .radio import Radio
    index=int(form.get("channel_index","-1")); name=form.get("channel_name","").strip(); secret_hex=form.get("channel_secret","").strip()
    if not 0<=index<=255 or not 1<=len(name.encode())<=32: raise ValueError("channel index must be 0-255 and name must be 1-32 bytes")
    if secret_hex:
     if len(secret_hex)!=32: raise ValueError("private channel secret must be 32 hexadecimal characters")
     secret=bytes.fromhex(secret_hex)
    elif name.startswith("#"): secret=None
    else: raise ValueError("use a #name for a public topic channel or provide a 32-character private secret")
    Radio(self.store.setting("port","/dev/ttyUSB0"),self.store.setting("baud","115200")).set_channel(index,name,secret)
   elif path=="/channel-delete":
    from .radio import Radio
    index=int(form.get("channel_index","-1"))
    if not 0<=index<=255: raise ValueError("channel index must be 0-255")
    Radio(self.store.setting("port","/dev/ttyUSB0"),self.store.setting("baud","115200")).delete_channel(index)
   elif path=="/save":
    hours=int(form.get("hours","0")); minutes=int(form.get("minutes","0"))
    if hours<0 or not 0<=minutes<=59: raise ValueError("hours must be non-negative and minutes must be 0-59")
    self.store.save({"name":form.get("name",""),"channel":form.get("channel",""),"message":form.get("message",""),"interval":hours*3600+minutes*60,"enabled":form.get("enabled")=="on"},form.get("id") or None)
   elif path=="/delete": self.store.delete(form["id"])
   elif path=="/toggle": self.store.toggle(form["id"])
   elif path=="/send":
    row=self.store.get(form["id"]); from .radio import Radio
    if not row: raise ValueError("schedule not found")
    send_message(Radio(self.store.setting("port","/dev/ttyUSB0"),self.store.setting("baud","115200")),row["channel"],row["message"])
   else: return self.send_error(404)
   self.send_response(303); self.send_header("Location","/"); self.end_headers()
  except Exception as exc: self.page(str(exc))
 def page(self,error=""):
  rows=self.store.schedules(); q=parse_qs(urlparse(self.path).query); edit=self.store.get(q["edit"][0]) if q.get("edit") else None; e=edit or {"id":"","name":"","channel":0,"message":"","interval":3600,"enabled":1}; hours,minutes=duration_parts(e["interval"])
  channels=[]; channel_error=""; radio_settings={}; radio_error=""
  try:
   from .radio import Radio
   radio_settings,channels=Radio(self.store.setting("port","/dev/ttyUSB0"),self.store.setting("baud","115200")).snapshot()
  except Exception as exc: radio_error=channel_error=str(exc)
  out='<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Towncrier</title><style>'+CSS+'</style></head><body><main><header><div class="eyebrow">MeshCore channel scheduler</div><h1>Towncrier</h1><p class="muted">Schedule clear, reliable channel messages from your MeshCore radio.</p></header><p class="muted">Network-accessible scheduler; no authentication. Use only on a trusted network.</p>'
  if error: out+='<p class="error">'+esc(error)+'</p>'
  out+='<h2>Radio settings</h2><form class="card" method="post" action="/radio-settings"><label>Australian MeshCore preset<select required name="preset">'+''.join('<option value="'+esc(name)+'">'+esc(name)+' — '+str(values[0])+' MHz / BW '+str(values[1])+' / SF '+str(values[2])+' / CR '+str(values[3])+'</option>' for name,values in RADIO_PRESETS.items())+'</select></label><p class="muted">Current: '+esc(radio_settings or radio_error or 'unavailable')+'</p><button>Apply preset to radio</button></form>'
  out+='<h2>Radio channels</h2><p>Channels are stored on the radio itself. Use a name beginning with <code>#</code> for a public topic channel, or enter a 32-character hexadecimal secret for a private channel.</p>'
  if channel_error: out+='<p class="error">Could not read channels: '+esc(channel_error)+'</p>'
  out+='<table><tr><th>Index</th><th>Name</th><th>State</th><th>Action</th></tr>'
  for channel in channels:
   out+='<tr><td>'+str(channel["index"])+'</td><td>'+esc(channel["name"] or '—')+'</td><td>'+('configured' if channel["configured"] else 'empty')+'</td><td>'+('<form class="inline" method="post" action="/channel-delete"><input type="hidden" name="channel_index" value="'+str(channel["index"])+ '"><button class="danger">delete</button></form>' if channel["configured"] else '<span class="muted">available</span>')+'</td></tr>'
  out+='</table><form class="card" method="post" action="/channel-save"><h3>Add or replace a channel</h3><div class="grid"><label>Slot<select required name="channel_index">'+''.join('<option value="'+str(c["index"])+ '">'+str(c["index"])+(' · '+esc(c["name"]) if c["configured"] else ' · empty')+'</option>' for c in channels)+'</select></label><label>Name<input required maxlength="32" name="channel_name"></label><label>Private secret<input name="channel_secret" maxlength="32" pattern="[0-9a-fA-F]{32}" placeholder="32 hex characters, blank for #name"></label></div><p class="muted">Use a name beginning with <code>#</code> for a public topic, or enter a private secret.</p><button>Save channel on radio</button></form>'
  out+='<form class="card" method="post" action="/settings"><h2>USB companion</h2><div class="grid"><label>Serial port <input name="port" value="'+esc(self.store.setting("port","/dev/ttyUSB0"))+'"></label> <label>Baud <input name="baud" type="number" value="'+esc(self.store.setting("baud","115200"))+'"></label></div><button>Save settings</button></form><h2>Schedules</h2><table><tr><th>Name</th><th>Channel</th><th>Interval</th><th>Status</th><th>Actions</th></tr>'
  for r in rows:
   out+='<tr><td>'+esc(r["name"])+'</td><td>'+str(r["channel"])+'</td><td>'+esc(duration_label(r["interval"]))+'</td><td>'+('enabled' if r["enabled"] else 'disabled')+' / '+esc(r["status"] or 'waiting')+' '+esc(r["last_error"] or '')+'</td><td><a href="/?edit='+str(r["id"])+ '">edit</a> <form class="inline" method="post" action="/toggle"><input type="hidden" name="id" value="'+str(r["id"])+ '"><button>toggle</button></form> <form class="inline" method="post" action="/send"><input type="hidden" name="id" value="'+str(r["id"])+ '"><button>send now</button></form> <form class="inline" method="post" action="/delete"><input type="hidden" name="id" value="'+str(r["id"])+ '"><button>delete</button></form></td></tr>'
  out+='</table><form class="card" method="post" action="/save"><h2>'+('Edit' if e["id"] else 'Add')+' schedule</h2><input type="hidden" name="id" value="'+esc(e["id"])+ '"><label>Name <input required name="name" value="'+esc(e["name"])+ '"></label> <label>Channel index <input required type="number" min="0" max="255" name="channel" value="'+str(e["channel"])+ '"></label> <label>Hours <input required type="number" min="0" name="hours" value="'+str(hours)+ '"></label> <label>Minutes <input required type="number" min="0" max="59" name="minutes" value="'+str(minutes)+ '"></label><br><small>Minimum schedule interval: 30 minutes.</small><br><label>Message <textarea required maxlength="500" name="message">'+esc(e["message"])+ '</textarea></label><br><small>Messages are sent as channel chunks of about 120 characters, never more than 130.</small><br><label><input type="checkbox" name="enabled" '+('checked' if e["enabled"] else '')+ '> enabled</label> <button>Save schedule</button></form></main></body></html>'
  self.respond(out)
 def respond(self,body,typ="text/html"):
  data=body.encode(); self.send_response(200); self.send_header("Content-Type",typ+"; charset=utf-8"); self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data)
 def log_message(self,*args): pass
def main(argv=None):
 p=argparse.ArgumentParser(); p.add_argument("--db",default="towncrier.db"); p.add_argument("--host",default="0.0.0.0"); p.add_argument("--port",type=int,default=8080); a=p.parse_args(argv); store=Store(a.db); Handler.store=store; stop=threading.Event(); Scheduler(store,stop).start(); server=ThreadingHTTPServer((a.host,a.port),Handler)
 try: server.serve_forever()
 except KeyboardInterrupt: pass
 finally: stop.set(); server.server_close()
if __name__=="__main__": main()
