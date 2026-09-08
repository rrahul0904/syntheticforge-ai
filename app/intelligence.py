from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta
from typing import Any

from .models import CorrelationSpec, TableSpec
from .privacy import classify_columns, synthesize_sensitive_value

CITY_STATE_ZIP={
    "MA":[("Boston","02108"),("Cambridge","02139"),("Canton","02021")],
    "NY":[("New York","10001"),("Buffalo","14201")],
    "CA":[("San Francisco","94105"),("Los Angeles","90001")],
    "TX":[("Austin","78701"),("Dallas","75201")],
}
COUNTRY_CURRENCY={"United States":"USD","Canada":"CAD","United Kingdom":"GBP","Australia":"AUD","Germany":"EUR","France":"EUR"}


def apply_sensitivity(table:TableSpec,rows:list[dict[str,Any]],seed:int=42,mode:str="fully-synthetic",source_rows:list[dict[str,Any]]|None=None)->None:
    classified=classify_columns(table.columns,source_rows)
    by_name={c.name:c for c in classified}
    table.columns=classified
    for i,row in enumerate(rows):
        for name,col in by_name.items():
            if col.sensitivity!="non-sensitive":
                source=(source_rows[i].get(name) if source_rows and i<len(source_rows) else None)
                row[name]=synthesize_sensitive_value(col,i,seed,mode,source)


def _weighted_choice(rng:random.Random,items:list[dict[str,Any]])->Any:
    if not items:return None
    total=sum(max(0,float(x.get("frequency",0))) for x in items)
    if total<=0:return items[0].get("value")
    needle=rng.random()*total; acc=0
    for item in items:
        acc+=max(0,float(item.get("frequency",0)))
        if acc>=needle:return item.get("value")
    return items[-1].get("value")


def apply_correlations(table:TableSpec,rows:list[dict[str,Any]],seed:int=42)->None:
    rng=random.Random(seed+991)
    by_name={c.name:c for c in table.columns}
    # Common high-value semantic relationships, even without a source profile.
    names={n.lower():n for n in by_name}
    if "state" in names and "city" in names:
        for row in rows:
            state=str(row.get(names["state"],""))
            if state not in CITY_STATE_ZIP: state=rng.choice(list(CITY_STATE_ZIP)); row[names["state"]]=state
            city,z=rng.choice(CITY_STATE_ZIP[state]); row[names["city"]]=city
            if "zip" in names: row[names["zip"]]=z
            if "postal_code" in names: row[names["postal_code"]]=z
    if "country" in names and "currency" in names:
        for row in rows:
            country=str(row.get(names["country"],""))
            if country not in COUNTRY_CURRENCY: country=rng.choice(list(COUNTRY_CURRENCY)); row[names["country"]]=country
            row[names["currency"]]=COUNTRY_CURRENCY[country]
    for corr in table.correlations:
        if len(corr.columns)<2:continue
        a,b=corr.columns[0],corr.columns[1]
        if a not in by_name or b not in by_name:continue
        if corr.kind=="conditional" and corr.mapping:
            for row in rows:
                options=corr.mapping.get(str(row.get(a)))
                if options: row[b]=_weighted_choice(rng,options)
        elif corr.kind=="pearson" and corr.coefficient is not None:
            sa=by_name[a].statistics; sb=by_name[b].statistics
            if not sa or not sb or sa.mean is None or sb.mean is None or not sa.stddev or not sb.stddev:continue
            rho=max(-.999,min(.999,float(corr.coefficient)))
            for row in rows:
                try: za=(float(row[a])-sa.mean)/sa.stddev
                except Exception: continue
                zb=rho*za+math.sqrt(1-rho*rho)*rng.gauss(0,1)
                value=sb.mean+zb*sb.stddev
                if sb.min_value is not None:value=max(float(sb.min_value),value)
                if sb.max_value is not None:value=min(float(sb.max_value),value)
                row[b]=round(value,2)
        elif corr.kind=="date-order":
            for row in rows:
                av=row.get(a)
                if not av:continue
                try:
                    start=datetime.fromisoformat(str(av).replace("Z","+00:00"))
                    row[b]=(start+timedelta(days=rng.randint(1,30))).isoformat(sep=" ",timespec="seconds")
                except ValueError:
                    try: start_d=date.fromisoformat(str(av)[:10]); row[b]=(start_d+timedelta(days=rng.randint(1,30))).isoformat()
                    except ValueError: pass


def apply_edge_cases(table:TableSpec,rows:list[dict[str,Any]],rate:float=0.0,negative_testing:bool=False,seed:int=42)->None:
    if rate<=0 or not rows:return
    rng=random.Random(seed+1777); count=max(1,round(len(rows)*rate)); indexes=list(range(len(rows)));rng.shuffle(indexes)
    for idx in indexes[:count]:
        row=rows[idx]
        protected_fk={name for fk in table.foreign_keys for name in fk.columns}
        candidates=[c for c in table.columns if not c.primary_key and (negative_testing or (c.name not in protected_fk and not c.unique))]
        if not candidates:continue
        col=rng.choice(candidates); t=col.data_type.lower()
        if col.nullable and rng.random()<.25: row[col.name]=None; continue
        if any(x in t for x in ["int","number","decimal","numeric","float","double"]):
            row[col.name]=col.min_value if rng.random()<.5 and col.min_value is not None else col.max_value if col.max_value is not None else (-1 if negative_testing else 0)
        elif any(x in t for x in ["char","text","string"]):
            samples=["","O'Reilly; DROP TABLE demo; --","Unicode café 東京 😀","   ","X"*(col.length or 128)]
            row[col.name]=rng.choice(samples)
        elif "date" in t or "time" in t: row[col.name]="1970-01-01"
    if negative_testing:
        # Deliberate violations are clearly isolated to explicit negative-testing mode.
        fk=next(iter(table.foreign_keys),None)
        if fk and indexes: rows[indexes[0]][fk.column]=-999999999
        required=next((c for c in table.columns if not c.nullable and not c.primary_key),None)
        if required and len(indexes)>1: rows[indexes[1]][required.name]=None
