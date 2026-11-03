import asyncio
class Radio:
 def __init__(self,port,baud): self.port,self.baud=port,int(baud)
 async def _send(self,channel,message):
  from meshcore import MeshCore,EventType
  mc=await MeshCore.create_serial(self.port,self.baud)
  try:
   result=await mc.commands.send_chan_msg(channel,message)
   if result.type==EventType.ERROR: raise RuntimeError(result.payload)
  finally: await mc.disconnect()
 def send(self,channel,message): asyncio.run(self._send(channel,message))
