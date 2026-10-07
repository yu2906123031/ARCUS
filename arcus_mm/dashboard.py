"""Loopback monitoring server. Stop requests use the engine's normal cleanup."""
import asyncio
import json
import secrets
import threading
import time
from decimal import Decimal as D
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

class Dashboard:
    def __init__(self, engine, loop, port, flatten=False):
        self.engine, self.loop, self.flatten = engine, loop, flatten
        self.engines = engine if isinstance(engine,list) else [engine]
        self.token = secrets.token_urlsafe(32)
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def reply(self, code, body, kind="application/json"):
                data = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", kind + "; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(data)
            def do_GET(self):
                if self.path == "/":
                    html = Path(__file__).with_name("compare.html" if isinstance(owner.engine,list) else "dashboard.html").read_text(encoding="utf-8")
                    self.reply(200, html.replace("__TOKEN__", owner.token), "text/html")
                elif self.path == "/api/status":
                    future = asyncio.run_coroutine_threadsafe(owner.snapshot(), owner.loop)
                    try: self.reply(200, json.dumps(future.result(timeout=2), default=str))
                    except Exception:
                        future.cancel()
                        self.reply(503, '{"error":"engine unavailable"}')
                else: self.reply(404, '{}')
            def do_POST(self):
                if self.path != "/api/stop": return self.reply(404, '{}')
                if not secrets.compare_digest(self.headers.get("X-Control-Token", ""), owner.token):
                    return self.reply(403, '{}')
                owner.loop.call_soon_threadsafe(owner.stop_all)
                self.reply(202, '{"accepted":true}')
        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
    def start(self):
        self.thread.start()
        print(f"Dashboard: http://127.0.0.1:{self.server.server_port}", flush=True)
    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
    def stop_all(self):
        for engine in self.engines:
            engine.trip("operator stop", self.flatten)

    async def snapshot(self):
        if isinstance(self.engine,list):
            return dict(mode="paper",accounts=[self.account_snapshot(e) for e in self.engines])
        return self.account_snapshot(self.engine)

    def account_snapshot(self,e):
        a=e.api; now=time.monotonic(); book=a.book; venue=e.venue
        fresh=a.fresh()
        protection_active=bool(e.live and e.c.server_protection and venue and venue.arm_time and now-venue.arm_time<e.c.protection_seconds)
        state="stopped" if e.stop.is_set() else "halting" if e.halt else "running" if fresh and venue and e.trading_enabled and now>=a.cooldown and (not e.live or not e.c.server_protection or protection_active) else "paused"
        ledger_equity=e.ledger.equity(book.mid) if book else e.ledger.initial
        account_equity=getattr(venue,"equity",None) if e.live else None
        risk_equity=min(ledger_equity,account_equity) if account_equity is not None else ledger_equity
        cap=max(D(0),risk_equity)*min(D(e.c.max_position_equity_fraction),D(e.c.leverage_cap))
        position=e.ledger.qty
        position_notional=abs(position)*book.mid if book else None
        loss=e.ledger.unrealized(book.bid if position>0 else book.ask) if book else None
        stop_budget=e.ledger.initial*D(e.c.stop_loss_equity_fraction)
        orders=list(venue.orders.values()) if venue else []
        buys=sum((o.remaining for o in orders if o.quote.side=="BUY" and not o.quote.reduce),D(0))
        sells=sum((o.remaining for o in orders if o.quote.side=="SELL" and not o.quote.reduce),D(0))
        required=getattr(a,"channels",set()); ready=getattr(a,"ready",set())
        account_qty=getattr(venue,"account_qty",None) if e.live else None
        account_seq=getattr(venue,"last_account_seq",None) if e.live else None
        fill_seq=getattr(venue,"last_fill_seq",None) if e.live else None
        synced=account_qty==position and account_seq>=fill_seq if account_qty is not None and account_seq is not None and fill_seq is not None else None
        recent=list(getattr(e.log,"recent",[]))
        # Only display selected structured fields, never credentials or request headers.
        fill_fields=("time_ns","side","quantity","price","fee","role","maker","position","trade_id")
        fills=[{k:r[k] for k in fill_fields if k in r} for r in recent if r.get("event")=="fill"][-30:][::-1]
        event_names={"halt","fatal","ws_connected","ws_disconnected","request_failed","mutation_rejected",
                     "software_protection_enabled","software_cancel_confirmed","software_cancel_failed","protection_armed","protection_disarmed","protection_retained","protection_quota_exhausted","rate_limit_pause","cancel_wait","mutation_uncertain","mutation_recovered","mutation_recovery_failed","quote_retry",
                     "auto_resume_selected","preflight","trading_enabled","cancel_all_confirmed","exit_attempt","clock_unavailable","order_state"}
        event_fields=("time_ns","event","reason","status","rejection_reason","action","status_code","consecutive","error_type","retry_seconds","retry_at_ms")
        events=[{k:r[k] for k in event_fields if k in r} for r in recent
                if r.get("event") in event_names and (r.get("event")!="order_state" or r.get("status")=="REJECTED")][-40:][::-1]
        return dict(state=state,mode="live" if e.live else "paper",market=e.c.market,strategy=e.c.strategy,
            initial_equity=e.ledger.initial,equity=ledger_equity,account_equity=account_equity,risk_equity=risk_equity,
            free_collateral=getattr(venue,"free_collateral",None) if e.live else None,
            halt=e.halt,connected=a.connected,market_fresh=fresh,
            pause_reason=("服务端保护今日 10 次自动触发配额已用尽，等待下一个 UTC 日；暂停交易" if e.c.server_protection and getattr(venue,"protection_block_reason",None)=="daily_trigger_limit" else
                          ("等待服务端保护启用，暂不发送交易订单" if e.c.server_protection else "等待账户核对与杠杆确认，软件监控就绪后开始交易") if e.live and venue and (not e.trading_enabled or (e.c.server_protection and not protection_active)) else
                          "限流冷却，暂停请求" if e.live and now<a.cooldown else
                          "等待有效行情或订阅就绪" if not fresh else None),
            uptime_seconds=round(now-e.started),clock_pending=a.clock_pending,clock_fault=a.clock_fault,
            offset_ms=a.offset_ns/1000000,clock_age_seconds=round(now-a.clock_checked,1) if a.clock_checked else None,
            book_age_seconds=round(now-book.received,2) if book else None,
            bid=book.bid if book else None,ask=book.ask if book else None,mid=book.mid if book else None,
            spread_bps=(book.ask-book.bid)/book.mid*10000 if book else None,
            bid_size=book.bid_size if book else None,ask_size=book.ask_size if book else None,
            orders=[dict(slot=o.quote.slot,side=o.quote.side,price=o.quote.price,quantity=o.quote.qty,
                         remaining=o.remaining,notional=o.remaining*o.quote.price,status=o.status,
                         reduce_only=o.quote.reduce,age_seconds=round(now-o.created,1),
                         client_id=o.client_id,order_id=o.order_id) for o in orders],
            position=position,average_entry=e.ledger.entry,position_notional=position_notional,
            metrics=e.ledger.report(book,e.taker_fee) if book and hasattr(e,"taker_fee") else {},
            risk=dict(leverage=e.c.leverage_cap,capital_cap=e.c.capital_cap,
                order_fraction=e.c.order_equity_fraction,position_fraction=e.c.max_position_equity_fraction,
                stop_fraction=e.c.stop_loss_equity_fraction,position_limit_usd=cap,
                position_usage_pct=position_notional/cap*100 if position_notional is not None and cap>0 else None,
                worst_buy_usd=abs(position+buys)*book.mid if book else None,
                worst_sell_usd=abs(position-sells)*book.mid if book else None,
                stop_budget_usd=stop_budget,floating_pnl_at_exit=loss,
                stop_usage_pct=max(D(0),-loss)/stop_budget*100 if loss is not None and stop_budget>0 else None,
                disconnect_seconds=e.c.disconnect_seconds,order_failures=getattr(venue,"failures",0),
                request_failures=e.failure_count,max_failures=e.c.max_order_failures,
                account_refresh_seconds=e.c.account_refresh_seconds),
            health=dict(trading_enabled=e.trading_enabled,
                protection_mode="server_and_software" if e.c.server_protection else "software_only",
                software_protection_active=bool(e.live and e.trading_enabled and not e.stop.is_set() and not e.halt),
                protection_active=protection_active,
                protection_block_reason=getattr(venue,"protection_block_reason",None),
                protection_retry_at_ms=getattr(venue,"protection_retry_at_ms",None),
                cooldown_seconds=round(max(0,a.cooldown-now),1),
                required_channels=sorted(required),ready_channels=sorted(ready),
                missing_channels=sorted(required-ready),account_synced=synced,
                account_position=account_qty,account_sequence=account_seq,fill_sequence=fill_seq,
                account_age_seconds=round(now-venue.account_time,1) if e.live and venue and venue.account_time else None,
                protection_age_seconds=round(now-venue.arm_time,1) if e.live and venue and venue.arm_time else None,
                protection_lead_seconds=e.c.protection_seconds if e.live and e.c.server_protection else None,
                protection_refresh_seconds=e.c.protection_refresh_seconds if e.live and e.c.server_protection else None,
                protection_remaining_seconds=round(max(0,e.c.protection_seconds-(now-venue.arm_time)),1) if e.live and venue and venue.arm_time else None,
                recovering=getattr(venue,"recovering",False),last_error=a.error),
            recovery=dict(enabled=e.auto_resume,resumed=bool(getattr(venue,"resume_info",None)),
                          info=getattr(venue,"resume_info",None)),
            fees=dict(maker=getattr(e,"maker_fee",None),taker=getattr(e,"taker_fee",None)),
            fills=fills,events=events,flatten_on_stop=self.flatten)
