import sys; sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import custom_signals as cs
from custom_signal_parser import target_weights
def mk(side="BUY", ot="limit", entry=100.0, sl=95.0, tps=(105.0,110.0,120.0), trigger=None, status="pending"):
    return {"id":"TC-1","symbol":"SOL/USD","base":"SOL","side":side,"order_type":ot,"entry":entry,"trigger":trigger,"sl":sl,
            "targets":list(tps),"weights":[float(w) for w in target_weights(len(tps))],"hit":[False]*len(tps),
            "status":status,"armed_ms":0,"mfe_r":0.0,"mae_r":0.0}
B=lambda *x:[(i*60000,h,l) for i,(h,l) in enumerate(x)]
def types(ev): return [e["type"] if e["type"]!="target" else f"T{e['index']+1}" for e in ev]
# 1 limit buy: fill, T1,T2,T3 -> all
t=mk(); ev=cs.advance(t,B((103,101),(101,99.5),(106,100),(111,105),(121,110)))
print(types(ev), t["status"], cs.final_r_for(t)); assert types(ev)==["fill","T1","T2","T3","all_targets"]
# weights .4/.3/.3, R: 1,2,4 => .4+.6+1.2=2.2
assert abs(cs.final_r_for(t)-2.2)<1e-9
# 2 stop before any target
t=mk(); ev=cs.advance(t,B((101,99),(100,94)))
print(types(ev), cs.final_r_for(t,stopped=True)); assert types(ev)==["fill","stop"] and cs.final_r_for(t,stopped=True)==-1.0
# 3 T1 then stop: .4*1 + .6*-1 = -0.2
t=mk(); ev=cs.advance(t,B((101,99),(106,100),(100,94)))
r=cs.final_r_for(t,stopped=True); print(types(ev),r); assert abs(r-(-0.2))<1e-9
# 4 same bar SL and TP -> SL first
t=mk(status="open"); ev=cs.advance(t,B((106,94)))
print(types(ev)); assert types(ev)==["stop"]
# 5 pending cancelled when T1 hit before fill (buy limit never touched)
t=mk(); ev=cs.advance(t,B((104,101),(106,102))); print(types(ev),t["status"]); assert types(ev)==["cancel"]
# 6 sell stop-limit
t=mk("SELL","stop_limit",entry=99.0,sl=105.0,tps=(95.0,90.0),trigger=100.0)
ev=cs.advance(t,B((101,99.5),(100,98),(99.5,94))); print(types(ev),t["status"]); assert types(ev)==["triggered","fill","T1"]
# 7 buy stop market
t=mk("BUY","stop_market",entry=100.0,sl=95.0,tps=(105.0,),trigger=100.0)
ev=cs.advance(t,B((99,97),(100.5,99),(106,100))); print(types(ev),t["status"]); assert types(ev)==["fill","T1","all_targets"]
# 8 reprocessing same bars is idempotent
t=mk(); bars=B((103,101),(106,100)); e1=cs.advance(t,bars); e2=cs.advance(t,bars); print(types(e1),types(e2)); assert e2==[] or types(e2)==[]
# 9 admin close
t=mk(status="open"); t["hit"][0]=True
r=cs.final_r_for(t,exit_price=103.0); print(r); assert abs(r-(0.4*1+0.6*0.6))<1e-9
# 10 sell all targets
t=mk("SELL","limit",entry=100.0,sl=105.0,tps=(95.0,90.0)); ev=cs.advance(t,B((100.5,99),(96,94),(92,89)))
print(types(ev), cs.final_r_for(t)); assert abs(cs.final_r_for(t)-(0.5*1+0.5*2))<1e-9
print("ALL TRACKER TESTS PASS")
