"""Arcus spot wire protocol, verified against captured 2026-09-29 responses."""
import copy
import json
import os
import re
import time
from dataclasses import dataclass
from decimal import Decimal as D
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import keccak
from arcus import Blocked, ReconcileError, decimal

BASE = 'https://router.spot.arcus.xyz'
RPC_DEFAULT = 'https://rpc.mainnet.chain.robinhood.com'
CHAIN = 4663
ZERO = '0x'+'0'*40
TYPES = {
 'PermitWitnessTransferFrom':[{'name':n,'type':t} for n,t in [('permitted','TokenPermissions'),('spender','address'),('nonce','uint256'),('deadline','uint256'),('witness','TakerIntent')]],
 'TokenPermissions':[{'name':'token','type':'address'},{'name':'amount','type':'uint256'}],
 'TakerIntent':[{'name':n,'type':t} for n,t in [('taker','address'),('takerSellToken','address'),('takerBuyToken','address'),('sellAmount','uint256'),('minBuyAmount','uint256'),('allowWrapped','bool'),('nonce','uint256'),('deadline','uint256')]]}

class TransportError(Blocked):
    pass

class FeePolicyError(Blocked):
    pass

def addr(value):
    if not isinstance(value,str) or not re.fullmatch(r'0x[0-9a-fA-F]{40}',value) or value.lower()==ZERO:
        raise Blocked('invalid address')
    return value.lower()

def uint(value):
    if isinstance(value,bool) or not re.fullmatch(r'[0-9]+',str(value)):
        raise Blocked('invalid uint256')
    result=int(value)
    if result >= 2**256: raise Blocked('uint256 overflow')
    return result

def atomic(amount, decimals):
    value=decimal(amount,positive=True)*D(10)**decimals
    if value!=value.to_integral_value():raise Blocked('amount exceeds token precision')
    return uint(int(value))

def human(value, decimals):
    return D(uint(value))/D(10)**decimals

def request(url, body=None):
    req=Request(url,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json','Accept':'application/json','User-Agent':'ArcusSpot/1.0'})
    try:
        with urlopen(req,timeout=10) as response:return json.load(response)
    except HTTPError as error:
        # Never include request payload, RPC URLs, signatures or raw error text.
        raise TransportError('HTTP_'+str(error.code)) from None
    except (URLError,TimeoutError,OSError,ValueError):
        raise TransportError('NETWORK_OR_JSON_FAILURE') from None

class Router:
    def get(self, path, params=None):
        from urllib.parse import urlencode
        return request(BASE+path+('?' + urlencode(params) if params else ''))
    def submit(self, body):
        # Exactly one POST. Caller journals first and resolves ambiguous outcomes.
        return request(BASE+'/v1/submit',body)

class Rpc:
    def __init__(self):self.url=os.getenv('ARCUS_RPC_URL',RPC_DEFAULT)
    def call(self, method, params):
        if not self.url.startswith('https://'):raise Blocked('RPC must use HTTPS')
        result=request(self.url,{'jsonrpc':'2.0','id':1,'method':method,'params':params})
        if 'error' in result or 'result' not in result:raise TransportError('RPC_ERROR')
        return result['result']
    def eth_call(self, token, data, block='latest'):
        return self.call('eth_call',[{'to':token,'data':data},block])
    def balance(self, token, wallet, block='latest'):
        return int(self.eth_call(token,'0x70a08231'+addr(wallet)[2:].zfill(64),block),16)
    def allowance(self,token,wallet,spender):
        return int(self.eth_call(token,'0xdd62ed3e'+addr(wallet)[2:].zfill(64)+addr(spender)[2:].zfill(64)),16)

@dataclass(frozen=True)
class Quote:
    side: str
    sell_token: str
    buy_token: str
    sell_raw: int
    buy_raw: int
    min_raw: int
    expiry: int
    typed_data: dict
    fees: list

class Spot:
    def __init__(self,config,router=None,rpc=None,wallet=None):
        self.c=config
        self.router=router or Router()
        self.rpc=rpc or Rpc()
        public_wallet=wallet or os.getenv('ARCUS_WALLET_ADDRESS','')
        self.wallet=addr(public_wallet) if public_wallet else None
        self.captures={}
        self.account=None
        deployment=self.router.get('/v1/deployment')
        if deployment['chainId']!=CHAIN or 'arcus' not in deployment['venues']:raise Blocked('wrong deployment')
        self.contracts=deployment['contracts']
        for key in ('permit2','arcusSettlement'):addr(self.contracts[key])
        rows=self.router.get('/v1/tokens',{'chainId':CHAIN})
        self.tokens={}
        for symbol in ('USDG','NVDA','SPY','QQQ'):
            found=[r for r in rows if r.get('symbol')==symbol and r.get('chainId')==CHAIN and r.get('verified') is True]
            if len(found)!=1:raise Blocked('missing or ambiguous token '+symbol)
            item=found[0];addr(item['address'])
            if type(item['decimals']) is not int or not 0<=item['decimals']<=36:raise Blocked('invalid decimals')
            self.tokens[symbol]=item
        self.meta={addr(v['address']):v for v in self.tokens.values()}
    def require_wallet(self):
        if not self.wallet:raise Blocked('ARCUS_WALLET_ADDRESS is missing; configure the public address only')
    def signer(self):
        self.require_wallet()
        if self.account:return self.account
        key,mnemonic=os.getenv('ARCUS_PRIVATE_KEY'),os.getenv('ARCUS_MNEMONIC')
        if bool(key)==bool(mnemonic):raise Blocked('configure exactly one ARCUS_PRIVATE_KEY or ARCUS_MNEMONIC locally')
        try:
            if key:self.account=Account.from_key(key)
            else:
                Account.enable_unaudited_hdwallet_features()
                self.account=Account.from_mnemonic(mnemonic,account_path=os.getenv('ARCUS_HD_PATH',"m/44'/60'/0'/0/0"))
        except Exception:raise Blocked('invalid signer configuration') from None
        if addr(self.account.address)!=self.wallet:
            self.account=None
            raise Blocked('signer differs from configured public wallet')
        return self.account
    def verify_chain(self):
        self.require_wallet()
        if int(self.rpc.call('eth_chainId',[]),16)!=CHAIN:raise ReconcileError('RPC chain mismatch')
        if self.rpc.call('eth_getCode',[self.wallet,'latest'])!='0x':raise Blocked('EOA required; delegated/smart wallets unsupported')
        for key in ('permit2','arcusSettlement'):
            if self.rpc.call('eth_getCode',[self.contracts[key],'latest'])=='0x':raise Blocked('deployment has no code')
        for symbol,token in self.tokens.items():
            if int(self.rpc.eth_call(token['address'],'0x313ce567'),16)!=token['decimals']:raise Blocked('on-chain decimals mismatch: '+symbol)
    def snapshot(self):
        self.require_wallet()
        block=self.rpc.call('eth_blockNumber',[])
        balances={symbol:self.rpc.balance(t['address'],self.wallet,block) for symbol,t in self.tokens.items()}
        for symbol,t in self.tokens.items():
            wrapped=t.get('wrappedTokenAddress')
            if wrapped and self.rpc.call('eth_getCode',[wrapped,block])!='0x':
                if self.rpc.balance(wrapped,self.wallet,block):raise ReconcileError('unexpected wrapped-token holding: '+symbol)
        balances['eth']=int(self.rpc.call('eth_getBalance',[self.wallet,block]),16)
        balances['block']=int(block,16)
        if balances['SPY'] or balances['QQQ']:raise ReconcileError('another allowed stock is already held')
        latest=self.rpc.call('eth_getTransactionCount',[self.wallet,'latest'])
        pending=self.rpc.call('eth_getTransactionCount',[self.wallet,'pending'])
        if latest!=pending:raise ReconcileError('wallet has pending transaction')
        return balances
    def params(self,side,amount):
        if side not in ('BUY','SELL'):raise Blocked('invalid direction')
        sell,buy=('USDG','NVDA') if side=='BUY' else ('NVDA','USDG')
        return {'chainId':CHAIN,'sellToken':self.tokens[sell]['address'],'buyToken':self.tokens[buy]['address'],
                'sellAmount':str(atomic(amount,self.tokens[sell]['decimals'])),'allowWrapped':'false','sourceInclude':'arcus'}
    def select(self,data):
        rows=[row for row in data.get('all',[]) if row.get('venue')=='arcus']
        if len(rows)!=1:raise Blocked('no unique Arcus spot quote')
        return rows[0]
    def price(self,side,amount):
        p=self.params(side,amount)
        response=self.router.get('/v1/price',p)
        self.captures['price_'+side]={'request':p,'response':response}
        row=self.select(response)
        if uint(row['sellAmount'])!=int(p['sellAmount']) or uint(row['buyAmount'])<=0:raise Blocked('bad price amounts')
        return human(row['buyAmount'],self.meta[addr(p['buyToken'])]['decimals'])
    def quote(self,side,amount):
        self.require_wallet()
        p=self.params(side,amount)
        p.update(taker=self.wallet,slippageBps=int(self.c['slippage_bps']))
        response=self.router.get('/v1/quote',p)
        self.captures['quote_'+side]={'request':p,'response':response}
        row=self.select(response)
        return self.parse_quote(row,p,side)
    def measure_round_trip(self,amount):
        spent=decimal(amount,positive=True)
        got=self.price('BUY',spent)
        back=self.price('SELL',got)
        return {'usdg_in':spent,'token_amount':got,'usdg_back':back,'round_trip_bps':(1-back/spent)*10000}
    def parse_quote(self,row,p,side,at=None):
        try:
            td=copy.deepcopy(row['toSign']);m=td['message'];w=m['witness'];domain=td['domain']
            if td['primaryType']!='PermitWitnessTransferFrom' or td['types']!=TYPES:raise Blocked('unexpected EIP-712 types')
            if set(domain)!={'name','chainId','verifyingContract'} or domain['name']!='Permit2' or domain['chainId']!=CHAIN or addr(domain['verifyingContract'])!=addr(self.contracts['permit2']):raise Blocked('invalid EIP-712 domain')
            if set(m)!={'permitted','spender','nonce','deadline','witness'} or set(m['permitted'])!={'token','amount'} or set(w)!={v['name'] for v in TYPES['TakerIntent']}:raise Blocked('unexpected signed fields')
            if addr(m['spender'])!=addr(self.contracts['arcusSettlement']) or addr(w['taker'])!=self.wallet:raise Blocked('wrong spender/taker')
            if addr(w['takerSellToken'])!=addr(p['sellToken']) or addr(w['takerBuyToken'])!=addr(p['buyToken']) or addr(m['permitted']['token'])!=addr(p['sellToken']):raise Blocked('wrong signed token pair')
            incoming=uint(p['sellAmount']);outgoing=uint(row['buyAmount']);minimum=uint(w['minBuyAmount'])
            if incoming<=0 or outgoing<=0 or any(uint(v)!=incoming for v in (row['sellAmount'],w['sellAmount'],m['permitted']['amount'])):raise Blocked('wrong signed amount')
            if not outgoing*(10000-int(self.c['slippage_bps']))//10000<=minimum<=outgoing or minimum<=0:raise Blocked('slippage outside configured bound')
            expiry=uint(row['expiry'])
            if w['allowWrapped'] is not False or uint(w['nonce'])!=uint(m['nonce']) or uint(w['deadline'])!=expiry or uint(m['deadline'])!=expiry:raise Blocked('wrong wrapping/nonce/deadline')
            if expiry<=(time.time() if at is None else at)+2:raise Blocked('expired or nearly expired quote')
            if uint(row['arcus']['minAmountOut'])!=minimum:raise Blocked('inconsistent minimum output')
            if not isinstance(row['fees'],list):raise Blocked('fees absent')
            for fee in row['fees']:
                uint(fee['amount']);addr(fee['token'])
            return Quote(side,addr(p['sellToken']),addr(p['buyToken']),incoming,outgoing,minimum,expiry,td,copy.deepcopy(row['fees']))
        except (KeyError,TypeError,ValueError):raise Blocked('unrecognized quote JSON') from None
    def fee_policy(self,q):
        if self.c.get('accept_quote_fees') is True:return
        if any(uint(f['amount'])>0 for f in q.fees):
            raise FeePolicyError('NONZERO_QUOTE_FEES: original zero-fee/ETH-gas constraint blocks entry')
    def output(self,q,minimum=False):
        return human(q.min_raw if minimum else q.buy_raw,self.meta[q.buy_token]['decimals'])
    def ensure_allowance(self,q):
        if self.rpc.allowance(q.sell_token,self.wallet,self.contracts['permit2'])<q.sell_raw:
            raise Blocked('insufficient Permit2 allowance; use explicit approve command')
    def signed_body(self,q):
        if q.expiry<=time.time()+2:raise Blocked('quote expired before signing')
        # Revalidate in-memory payload before it reaches the signer.
        p={'sellToken':q.sell_token,'buyToken':q.buy_token,'sellAmount':str(q.sell_raw)}
        row={'toSign':q.typed_data,'sellAmount':str(q.sell_raw),'buyAmount':str(q.buy_raw),'expiry':q.expiry,'fees':q.fees,'arcus':{'minAmountOut':str(q.min_raw)}}
        self.parse_quote(row,p,q.side)
        message=encode_typed_data(full_message=q.typed_data)
        account=self.signer();signed=account.sign_message(message)
        if addr(Account.recover_message(message,signature=signed.signature))!=self.wallet:raise Blocked('signature recovery mismatch')
        return {'venue':'arcus','chainId':CHAIN,'taker':self.wallet,'signature':'0x'+bytes(signed.signature).hex(),'typedData':q.typed_data,'buyToken':q.buy_token,'builderFeeBps':0}
    def status(self,txhash):
        if not re.fullmatch('0x[0-9a-fA-F]{64}',txhash):raise Blocked('invalid tx hash')
        return self.router.get('/v1/status',{'chainId':CHAIN,'venue':'arcus','id':txhash})
    def transfer_deltas(self,receipt):
        topic='0x'+keccak(text='Transfer(address,address,uint256)').hex()
        result={symbol:0 for symbol in self.tokens}
        for log in receipt['logs']:
            topics=log.get('topics',[])
            if len(topics)!=3 or topics[0].lower()!=topic.lower():continue
            for symbol,token in self.tokens.items():
                if addr(log['address'])!=addr(token['address']):continue
                value=int(log['data'],16)
                if topics[1][-40:].lower()==self.wallet[2:]:result[symbol]-=value
                if topics[2][-40:].lower()==self.wallet[2:]:result[symbol]+=value
        return result
