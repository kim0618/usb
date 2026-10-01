from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
from app.crypto.live.models import IncomeRow, UserTrade
from app.crypto.live.performance import ManualLivePerformance
KST=ZoneInfo("Asia/Seoul")
def ms(v): return int(datetime.fromisoformat(v).replace(tzinfo=KST).timestamp()*1000)
def fill(i,at,order=None,pnl="0",fee="0.1",buyer=True):
    return UserTrade(i,i if order is None else order,"BTCUSDT","BUY" if buyer else "SELL","BOTH",Decimal("1"),Decimal("1"),Decimal("1"),Decimal(pnl),Decimal(fee),"USDT",False,buyer,ms(at))
def fund(i,at,n): return IncomeRow("BTCUSDT",IncomeRow.FUNDING,Decimal(n),"USDT","",ms(at),i)
class Reader:
    def __init__(self,trades=(),clients=None,funds=()):
        self.trades,self.clients,self.funds=list(trades),clients or {},list(funds); self.calls=[]; self.fail=set()
    def trade_history(self,*,from_id,limit):
        self.calls.append(from_id); return [x for x in self.trades if x.id>=from_id][:limit]
    def order(self,order_id):
        if order_id in self.fail: raise RuntimeError()
        return {"clientOrderId":self.clients.get(order_id,"external")}
    def income_window(self,*,start_ms,end_ms,income_type):
        return [x for x in self.funds if start_ms<=x.time_ms<=end_ms]
def service(path,r): return ManualLivePerformance(reader=r,path=path/"p.json",krw_rate=lambda:Decimal("1400"))

def test_empty_and_kst_calendar_boundaries(tmp_path):
    assert not service(tmp_path,Reader()).refresh(now_ms=ms("2026-10-01T00:00:00"),force=True)["has_trades"]
    for start,now,day in [("2026-09-29T23:59:59","2026-09-30T00:00:00",2),("2026-12-31T12:00:00","2027-01-01T00:00:00",2)]:
        r=Reader([fill(1,start)],{1:"usbm-open"}); got=service(tmp_path,r).refresh(now_ms=ms(now),force=True)
        assert got["first_trade_kst_date"]==start[:10] and got["running_day"]==day
        (tmp_path/"p.json").unlink()

def test_net_today_is_realized_less_fees_plus_safe_funding(tmp_path):
    rows=[fill(1,"2026-09-30T23:59:59",pnl="2",fee=".2"),fill(2,"2026-10-01T01:00:00",pnl="5",fee=".3",buyer=False)]
    r=Reader(rows,{1:"usbm-open",2:"usbm-close"},[fund("x","2026-10-01T00:30:00",".5")])
    got=service(tmp_path,r).refresh(now_ms=ms("2026-10-01T12:00:00"),force=True)
    assert got["cumulative_net_usdt"]==Decimal("7") and got["today_net_usdt"]==Decimal("5.2")
    assert got["cumulative_net_krw"]==Decimal("9800")

def test_namespace_isolation_and_external_overlap_funding(tmp_path):
    rows=[fill(1,"2026-10-01T01:00:00",pnl="4"),fill(2,"2026-10-01T02:00:00",pnl="99"),fill(3,"2026-10-01T03:00:00",buyer=False)]
    r=Reader(rows,{1:"usbm-close-guard",2:"auto-live",3:"external"},[fund("x","2026-10-01T02:30:00","9")])
    got=service(tmp_path,r).refresh(now_ms=ms("2026-10-01T04:00:00"),force=True)
    assert got["manual_trade_count"]==1 and got["attributed_funding"]==0 and got["cumulative_net_usdt"]==Decimal("3.9")

def test_pagination_persistence_dedup_and_retry(tmp_path):
    rows=[fill(i,"2026-10-01T01:00:00",10,fee="0") for i in range(1001)]
    r=Reader(rows,{10:"usbm-open"}); first=service(tmp_path,r).refresh(now_ms=ms("2026-10-01T02:00:00"),force=True)
    assert first["manual_trade_count"]==1001 and r.calls==[0,1000]
    assert service(tmp_path,r).refresh(now_ms=ms("2026-10-01T03:00:00"),force=True)["manual_trade_count"]==1001
    path=tmp_path/"retry"; path.mkdir(); retry=Reader([fill(1,"2026-10-01T01:00:00")],{1:"usbm-open"}); retry.fail.add(1)
    assert not service(path,retry).refresh(now_ms=ms("2026-10-01T02:00:00"),force=True)["classification_complete"]
    retry.fail.clear(); assert service(path,retry).refresh(now_ms=ms("2026-10-01T03:00:00"),force=True)["manual_trade_count"]==1
