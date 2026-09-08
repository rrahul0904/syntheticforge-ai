from __future__ import annotations

import math
import re
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any

from .models import ColumnSpec, ColumnStatistics, CorrelationSpec, TableProfile

EMAIL_RE=re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE=re.compile(r"^\+?[\d\s().-]{7,}$")
UUID_RE=re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}$")


def _is_number(v: Any) -> bool:
    return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(float(v))


def _to_number(v: Any) -> float | None:
    if _is_number(v): return float(v)
    return None


def _quantile(values: list[float], q: float) -> float:
    if not values: return 0.0
    vals=sorted(values); pos=(len(vals)-1)*q; lo=int(pos); hi=min(lo+1,len(vals)-1); frac=pos-lo
    return vals[lo]*(1-frac)+vals[hi]*frac


def _histogram(values: list[float], bins: int=10) -> list[dict[str,Any]]:
    if not values: return []
    lo=min(values); hi=max(values)
    if lo==hi: return [{"min":lo,"max":hi,"count":len(values)}]
    width=(hi-lo)/bins; counts=[0]*bins
    for v in values:
        idx=min(bins-1,int((v-lo)/width)); counts[idx]+=1
    return [{"min":round(lo+i*width,6),"max":round(lo+(i+1)*width,6),"count":counts[i]} for i in range(bins)]


def _pattern(value: str) -> str:
    if EMAIL_RE.match(value): return "email"
    if UUID_RE.match(value): return "uuid"
    if PHONE_RE.match(value): return "phone"
    if re.fullmatch(r"\d+",value): return "digits"
    if re.fullmatch(r"[A-Z]{2,6}-?\d{3,12}",value): return "business-code"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[ T].*)?",value): return "iso-date-time"
    shape=[]; last=None
    for ch in value[:64]:
        cur="A" if ch.isupper() else "a" if ch.islower() else "9" if ch.isdigit() else ch
        if cur!=last: shape.append(cur); last=cur
    return "shape:"+"".join(shape[:20])


def profile_column(values: list[Any]) -> ColumnStatistics:
    count=len(values); non_null=[v for v in values if v is not None]; null_count=count-len(non_null)
    stats=ColumnStatistics(count=count,null_count=null_count,null_rate=(null_count/count if count else 0),distinct_count=len({repr(v) for v in non_null}))
    if not non_null: return stats
    nums=[float(v) for v in non_null if _is_number(v)]
    if len(nums)>=max(2,int(len(non_null)*0.8)):
        stats.min_value=min(nums); stats.max_value=max(nums); stats.mean=statistics.fmean(nums); stats.median=statistics.median(nums)
        stats.stddev=statistics.pstdev(nums) if len(nums)>1 else 0.0
        if stats.stddev and stats.stddev > 0:
            stats.skewness=sum(((v-stats.mean)/stats.stddev)**3 for v in nums)/len(nums)
        else:
            stats.skewness=0.0
        stats.quantiles={"p05":_quantile(nums,.05),"p25":_quantile(nums,.25),"p50":_quantile(nums,.5),"p75":_quantile(nums,.75),"p95":_quantile(nums,.95)}
        stats.histogram=_histogram(nums)
    strings=[str(v) for v in non_null if isinstance(v,(str,date,datetime))]
    if strings:
        lengths=[len(v) for v in strings]; stats.min_length=min(lengths); stats.max_length=max(lengths); stats.mean_length=statistics.fmean(lengths)
        pats=Counter(_pattern(v) for v in strings); stats.patterns=[p for p,_ in pats.most_common(5)]
        prefix_counts=Counter(v[:3] for v in strings if len(v)>=3); suffix_counts=Counter(v[-3:] for v in strings if len(v)>=3)
        stats.prefixes=[v for v,_ in prefix_counts.most_common(5)]; stats.suffixes=[v for v,_ in suffix_counts.most_common(5)]
        parsed=[]
        for value in strings:
            try:
                parsed.append(datetime.fromisoformat(value.replace("Z","+00:00")))
            except (ValueError,TypeError):
                try: parsed.append(datetime.combine(date.fromisoformat(value[:10]),datetime.min.time()))
                except (ValueError,TypeError): pass
        if parsed:
            stats.hour_histogram={str(k):v for k,v in sorted(Counter(x.hour for x in parsed).items())}
            stats.weekday_histogram={str(k):v for k,v in sorted(Counter(x.weekday() for x in parsed).items())}
            stats.month_histogram={str(k):v for k,v in sorted(Counter(x.month for x in parsed).items())}
    top=Counter(repr(v) for v in non_null).most_common(20)
    stats.top_values=[{"value":v,"count":c,"frequency":c/len(non_null)} for v,c in top]
    return stats


def _pearson(xs:list[float],ys:list[float]) -> float | None:
    if len(xs)<3 or len(xs)!=len(ys): return None
    mx=statistics.fmean(xs); my=statistics.fmean(ys)
    dx=[x-mx for x in xs]; dy=[y-my for y in ys]
    denom=math.sqrt(sum(x*x for x in dx)*sum(y*y for y in dy))
    if denom==0: return None
    return sum(a*b for a,b in zip(dx,dy))/denom


def infer_correlations(rows:list[dict[str,Any]], columns:list[ColumnSpec]) -> list[CorrelationSpec]:
    result:list[CorrelationSpec]=[]
    names=[c.name for c in columns]
    numeric=[]
    for name in names:
        vals=[r.get(name) for r in rows if r.get(name) is not None]
        if vals and sum(_is_number(v) for v in vals)/len(vals)>=.8: numeric.append(name)
    for i,a in enumerate(numeric[:25]):
        for b in numeric[i+1:25]:
            pairs=[(float(r[a]),float(r[b])) for r in rows if _is_number(r.get(a)) and _is_number(r.get(b))]
            if len(pairs)>=3:
                corr=_pearson([p[0] for p in pairs],[p[1] for p in pairs])
                if corr is not None and abs(corr)>=.2:
                    result.append(CorrelationSpec(columns=[a,b],kind="pearson",coefficient=round(corr,6)))
    # Practical conditional dependencies for low-cardinality categorical columns.
    cat=[]
    for name in names:
        vals=[r.get(name) for r in rows if r.get(name) is not None]
        distinct=len({repr(v) for v in vals})
        if vals and 1<distinct<=min(100,max(10,len(vals)//2)): cat.append(name)
    for a in cat[:15]:
        for b in cat[:15]:
            if a==b: continue
            mapping=defaultdict(Counter)
            for r in rows:
                if r.get(a) is not None and r.get(b) is not None: mapping[str(r[a])][str(r[b])]+=1
            if not mapping: continue
            dominance=[]; packed={}
            for key,counter in mapping.items():
                total=sum(counter.values()); best=counter.most_common(1)[0][1]/total; dominance.append(best)
                packed[key]=[{"value":v,"frequency":c/total} for v,c in counter.most_common(10)]
            if dominance and statistics.fmean(dominance)>=.7:
                result.append(CorrelationSpec(columns=[a,b],kind="conditional",mapping=packed))
    # Common date dependencies.
    lowers={n.lower():n for n in names}
    for a,b in [("check_in_date","check_out_date"),("start_date","end_date"),("created_at","updated_at"),("payment_date","refund_date")]:
        if a in lowers and b in lowers: result.append(CorrelationSpec(columns=[lowers[a],lowers[b]],kind="date-order"))
    return result


def profile_rows(rows:list[dict[str,Any]], columns:list[ColumnSpec]|None=None, max_sample_rows:int=10_000) -> TableProfile:
    sampled=rows[:max_sample_rows]
    if columns is None:
        names=list(sampled[0].keys()) if sampled else []
        columns=[ColumnSpec(name=n) for n in names]
    stats={c.name:profile_column([r.get(c.name) for r in sampled]) for c in columns}
    return TableProfile(row_count=len(rows),sampled_rows=len(sampled),columns=stats,correlations=infer_correlations(sampled,columns))


def apply_profile_to_columns(columns:list[ColumnSpec], profile:TableProfile) -> list[ColumnSpec]:
    result=[]
    for col in columns:
        c=col.model_copy(deep=True); st=profile.columns.get(col.name)
        if st:
            c.statistics=st; c.null_rate=st.null_rate
            if st.min_value is not None: c.min_value=float(st.min_value)
            if st.max_value is not None: c.max_value=float(st.max_value)
            if st.max_length and c.length is None: c.length=st.max_length
            # Preserve actual categorical values only when cardinality is bounded and source is not sensitive.
            if st.distinct_count and st.distinct_count<=50 and c.sensitivity=="non-sensitive":
                vals=[]
                for item in st.top_values:
                    raw=item["value"]
                    try:
                        import ast; vals.append(ast.literal_eval(raw))
                    except Exception: vals.append(raw)
                if vals: c.choices=vals
        result.append(c)
    return result
