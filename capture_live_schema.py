import json,time
from pathlib import Path
from arcus import tokens,get,write
assets=tokens();write('tokens.snapshot.json',assets)
p={'chainId':4663,'sellToken':assets['USDG']['address'],'buyToken':assets['NVDA']['address'],'sellAmount':'10000000','allowWrapped':'false','sourceInclude':'arcus'}
for endpoint in ('price','quote'):
 args=dict(p)
 if endpoint=='quote':args.update(taker='0x70997970C51812dc3A010C7d01b50e0d17dc79C8',slippageBps=30)
 r=get(endpoint,args);write('research/'+endpoint+'.json',{'probe_only':True,'taker_is_official_example':True,'captured_epoch':time.time(),'request':args,**r})
 print(endpoint,json.dumps(r))
