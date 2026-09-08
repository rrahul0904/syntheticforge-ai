from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from .models import GeneratedTable, SystemGenerateRequest, ValidationCheck, ValidationReport
from .profiling import _pearson
from .rules import infer_business_rules, validate_rule


def _key(name:str)->str:return name.split(".")[-1].strip('"`[]').lower()


def _fail(name:str,details:str,**kw)->ValidationCheck:
    return ValidationCheck(name=name,validator=kw.pop("validator",name),passed=False,details=details,remediation=kw.pop("remediation","Review generation constraints and source metadata"),**kw)


def _pass(name:str,details:str,**kw)->ValidationCheck:
    return ValidationCheck(name=name,validator=kw.pop("validator",name),passed=True,details=details,**kw)


def _check_simple_expression(expr:str,row:dict[str,Any])->bool|None:
    # Safe evaluator for common CHECK forms: col >= number, col IN (...), col IS NOT NULL.
    m=re.fullmatch(r"\s*([A-Za-z_][\w]*)\s*(>=|<=|>|<|=|!=|<>)\s*(-?\d+(?:\.\d+)?|'[^']*')\s*",expr,re.I)
    if m:
        col,op,raw=m.groups(); val=row.get(col)
        if val is None:return True
        target=raw.strip("'") if raw.startswith("'") else float(raw)
        try:
            if isinstance(target,float): val=float(val)
            return {">=":val>=target,"<=":val<=target,">":val>target,"<":val<target,"=":val==target,"!=":val!=target,"<>":val!=target}[op]
        except Exception:return None
    m=re.fullmatch(r"\s*([A-Za-z_][\w]*)\s+IS\s+NOT\s+NULL\s*",expr,re.I)
    if m:return row.get(m.group(1)) is not None
    m=re.fullmatch(r"\s*([A-Za-z_][\w]*)\s+IN\s*\((.*)\)\s*",expr,re.I)
    if m:
        allowed=[x.strip().strip("'\"") for x in m.group(2).split(",")]; return str(row.get(m.group(1))) in allowed
    return None


def validate_system(req:SystemGenerateRequest,tables:list[GeneratedTable])->ValidationReport:
    checks:list[ValidationCheck]=[]; by_name={_key(t.name):t for t in tables}; specs={_key(t.name):t for t in req.tables}
    # NOT NULL
    failures=[]
    for t in tables:
        for c in t.columns:
            if not c.nullable:
                bad=sum(r.get(c.name) is None for r in t.rows)
                if bad:failures.append(f"{t.name}.{c.name}: {bad}")
    checks.append(_pass("not_null","All required values populated",observed=0,expected=0) if not failures else _fail("not_null","; ".join(failures[:10]),observed=failures,expected=0))
    # Primary/composite uniqueness.
    failures=[]
    for t in tables:
        spec=specs.get(_key(t.name)); pk=spec.primary_key_columns if spec else [c.name for c in t.columns if c.primary_key]
        if not pk: pk=[c.name for c in t.columns if c.primary_key]
        if pk:
            vals=[tuple(r.get(c) for c in pk) for r in t.rows]
            if any(any(v is None for v in key) for key in vals) or len(vals)!=len(set(map(repr,vals))):failures.append(f"{t.name}({','.join(pk)})")
    checks.append(_pass("primary_key_uniqueness","Primary keys are non-null and unique") if not failures else _fail("primary_key_uniqueness","Failed: "+", ".join(failures)))
    # Unique constraints.
    failures=[]
    for t in tables:
        spec=specs.get(_key(t.name)); constraints=list(spec.unique_constraints if spec else [])
        constraints += [type("U",(),{"name":c.name,"columns":[c.name]}) for c in t.columns if c.unique and not c.primary_key]
        for u in constraints:
            vals=[tuple(r.get(c) for c in u.columns) for r in t.rows]; vals=[v for v in vals if not any(x is None for x in v)]
            if len(vals)!=len(set(map(repr,vals))):failures.append(f"{t.name}.{getattr(u,'name',None) or ','.join(u.columns)}")
    checks.append(_pass("unique_constraints","Unique constraints preserved") if not failures else _fail("unique_constraints","Failed: "+", ".join(failures)))
    # FK including composites.
    failures=[]; count=0
    for t in tables:
        for fk in t.foreign_keys:
            parent=by_name.get(_key(fk.references_table)); count+=1
            if not parent:continue
            parent_keys={tuple(r.get(c) for c in fk.references_columns) for r in parent.rows}
            invalid=0
            for row in t.rows:
                key=tuple(row.get(c) for c in fk.columns)
                if all(v is None for v in key):continue
                if key not in parent_keys:invalid+=1
            if invalid:failures.append(f"{t.name}.{'+'.join(fk.columns)}: {invalid} orphan(s)")
    checks.append(_pass("foreign_key_integrity",f"{count} relationship(s) validated",observed=0,expected=0) if not failures else _fail("foreign_key_integrity","; ".join(failures[:10]),observed=failures,expected=0))
    # Enum/domain and numeric/string constraints.
    enum_fail=[]; range_fail=[]; length_fail=[]
    for t in tables:
        for c in t.columns:
            allowed=c.choices or c.enum_values
            if allowed:
                bad=sum(r.get(c.name) is not None and r.get(c.name) not in allowed for r in t.rows)
                if bad:enum_fail.append(f"{t.name}.{c.name}:{bad}")
            if c.min_value is not None or c.max_value is not None:
                bad=0
                for r in t.rows:
                    v=r.get(c.name)
                    if v is None:continue
                    try: bad+=int((c.min_value is not None and float(v)<c.min_value) or (c.max_value is not None and float(v)>c.max_value))
                    except Exception:pass
                if bad:range_fail.append(f"{t.name}.{c.name}:{bad}")
            if c.length:
                bad=sum(r.get(c.name) is not None and len(str(r.get(c.name)))>c.length for r in t.rows)
                if bad:length_fail.append(f"{t.name}.{c.name}:{bad}")
    checks.append(_pass("enum_membership","Enum/domain values valid") if not enum_fail else _fail("enum_membership","; ".join(enum_fail)))
    checks.append(_pass("numeric_ranges","Numeric ranges respected") if not range_fail else _fail("numeric_ranges","; ".join(range_fail)))
    checks.append(_pass("string_lengths","String lengths respected") if not length_fail else _fail("string_lengths","; ".join(length_fail)))
    # Check constraints safely evaluable.
    check_fail=[]; unsupported=0
    for t in tables:
        spec=specs.get(_key(t.name));
        if not spec:continue
        for constraint in spec.check_constraints:
            bad=0;evaluated=0
            for r in t.rows:
                val=_check_simple_expression(constraint.expression,r)
                if val is None:continue
                evaluated+=1;bad+=0 if val else 1
            if not evaluated:unsupported+=1
            elif bad:check_fail.append(f"{t.name}.{constraint.name or 'check'}:{bad}")
    checks.append(_pass("check_constraints",f"Check constraints passed; {unsupported} unsupported expression(s) skipped",severity="warning" if unsupported else "error") if not check_fail else _fail("check_constraints","; ".join(check_fail)))
    # Legacy-compatible date coherence summary (also covered by business-rule checks below).
    date_fail=[]
    for t in tables:
        names={c.name for c in t.columns}
        for start_col,end_col in [("check_in_date","check_out_date"),("start_date","end_date")]:
            if {start_col,end_col}.issubset(names):
                bad=0
                for r in t.rows:
                    a,b=r.get(start_col),r.get(end_col)
                    if not a or not b: continue
                    try:
                        ad=date.fromisoformat(str(a)[:10]); bd=date.fromisoformat(str(b)[:10]); bad += int(bd <= ad)
                    except ValueError: pass
                if bad: date_fail.append(f"{t.name}.{start_col}->{end_col}:{bad}")
    checks.append(_pass("date_coherence","Known date dependencies are coherent") if not date_fail else _fail("date_coherence","; ".join(date_fail)))

    # Cross-table and financial business semantics.
    semantic_fail=[]; semantic_checked=0
    for t in tables:
        spec=specs.get(_key(t.name))
        if not spec: continue
        for fk in t.foreign_keys:
            if len(fk.columns)!=1 or len(fk.references_columns)!=1: continue
            parent=by_name.get(_key(fk.references_table))
            if not parent: continue
            pmap={r.get(fk.references_column):r for r in parent.rows}
            for row in t.rows:
                prow=pmap.get(row.get(fk.column))
                if not prow: continue
                if "amount" in row:
                    parent_amount=next((x for x in [prow.get("total_amount"),prow.get("amount"),prow.get("subtotal")] if x is not None),None)
                    if parent_amount is not None:
                        try:
                            semantic_checked+=1
                            if float(row["amount"])>float(parent_amount)+0.01: semantic_fail.append(f"{t.name}.amount>{parent.name}")
                        except Exception: pass
                if "refunded_at" in row and prow.get("paid_at") and row.get("refunded_at"):
                    try:
                        semantic_checked+=1
                        if datetime.fromisoformat(str(row["refunded_at"]).replace("Z","+00:00")) < datetime.fromisoformat(str(prow["paid_at"]).replace("Z","+00:00")): semantic_fail.append(f"{t.name}.refunded_at before payment")
                    except ValueError: pass
        names={c.name for c in t.columns}
        if {"subtotal","tax_amount","total_amount"}.issubset(names):
            for row in t.rows:
                try:
                    semantic_checked+=1
                    if abs((float(row["subtotal"])+float(row["tax_amount"]))-float(row["total_amount"]))>.02: semantic_fail.append(f"{t.name}.total identity")
                except Exception: pass
        if {"quantity","unit_price","line_total"}.issubset(names):
            for row in t.rows:
                try:
                    semantic_checked+=1
                    if abs(float(row["quantity"])*float(row["unit_price"])-float(row["line_total"]))>.02: semantic_fail.append(f"{t.name}.line total identity")
                except Exception: pass
    checks.append(_pass("cross_table_business_semantics",f"{semantic_checked} semantic assertion(s) validated",observed=0,expected=0) if not semantic_fail else _fail("cross_table_business_semantics","; ".join(semantic_fail[:10]),observed=semantic_fail,expected=0))

    # Business rules incl date ordering.
    for t in tables:
        spec=specs.get(_key(t.name));
        if not spec:continue
        for rule in infer_business_rules(spec):checks.append(validate_rule(rule,t.rows,t.name))
    # Scenario exact percentage.
    if req.scenario:
        m=re.search(r"(\d+(?:\.\d+)?)\s*%[^.\n]{0,60}(cancelled|canceled|cancellations?)",req.scenario,re.I) or re.search(r"(cancelled|canceled|cancellations?)[^.\n]{0,60}?(\d+(?:\.\d+)?)\s*%",req.scenario,re.I)
        if m:
            pct=float(m.group(1) if m.group(1)[0].isdigit() else m.group(2))/100; status_rows=[]
            for t in tables:
                status_spec=next((c for c in t.columns if "status" in c.name.lower()),None)
                if status_spec and ((status_spec.choices and any(str(v).lower() in {"cancelled","canceled"} for v in status_spec.choices)) or (not status_spec.choices and any(token in t.name.lower() for token in ["reservation","booking","order","subscription"]))):
                    status_rows.extend(t.rows)
            if status_rows:
                observed=sum(str(r.get(next((k for k in r if "status" in k.lower()),""),"")).lower() in {"cancelled","canceled"} for r in status_rows)/len(status_rows)
                ok=abs(observed-pct)<=max(.005,1/len(status_rows)); checks.append(_pass("scenario_percentages",f"Cancellation rate {observed:.3%}",expected=pct,observed=observed) if ok else _fail("scenario_percentages",f"Expected {pct:.3%}, observed {observed:.3%}",expected=pct,observed=observed))
    # Distribution similarity when source-profile statistics are attached to the model.
    # This intentionally compares robust summaries instead of attempting to reproduce source rows.
    for t in tables:
        spec=specs.get(_key(t.name))
        if not spec or not t.rows: continue
        source_cols={c.name:c for c in spec.columns}
        for name,col in source_cols.items():
            st=col.statistics
            if not st: continue
            values=[r.get(name) for r in t.rows]
            observed_null=sum(v is None for v in values)/len(values)
            null_tol=max(.03,2/len(values))
            checks.append(_pass("distribution_null_rate",f"{t.name}.{name}: target={st.null_rate:.3f}, observed={observed_null:.3f}",table=t.name,column=name,expected=st.null_rate,observed=observed_null) if abs(observed_null-st.null_rate)<=null_tol else _fail("distribution_null_rate",f"{t.name}.{name}: target={st.null_rate:.3f}, observed={observed_null:.3f}",table=t.name,column=name,expected=st.null_rate,observed=observed_null))
            nums=[]
            for v in values:
                try:
                    if v is not None: nums.append(float(v))
                except (TypeError,ValueError): pass
            if nums and st.mean is not None:
                observed_mean=sum(nums)/len(nums); scale=max(abs(float(st.mean)),float(st.stddev or 0),1.0); tolerance=.22*scale
                checks.append(_pass("distribution_mean",f"{t.name}.{name}: target={st.mean:.3f}, observed={observed_mean:.3f}",table=t.name,column=name,expected=st.mean,observed=observed_mean) if abs(observed_mean-float(st.mean))<=tolerance else _fail("distribution_mean",f"{t.name}.{name}: target={st.mean:.3f}, observed={observed_mean:.3f}",table=t.name,column=name,expected=st.mean,observed=observed_mean))
            if st.top_values and st.distinct_count and st.distinct_count<=50:
                target={str(x.get("value")):float(x.get("frequency",0)) for x in st.top_values}
                observed={}
                nonnull=[v for v in values if v is not None]
                if nonnull:
                    from collections import Counter
                    counts=Counter(repr(v) for v in nonnull); observed={k:v/len(nonnull) for k,v in counts.items()}
                    tv=.5*sum(abs(target.get(k,0)-observed.get(k,0)) for k in set(target)|set(observed))
                    checks.append(_pass("distribution_categorical",f"{t.name}.{name}: total variation={tv:.3f}",table=t.name,column=name,expected="<=0.25",observed=tv) if tv<=.25 else _fail("distribution_categorical",f"{t.name}.{name}: total variation={tv:.3f}",table=t.name,column=name,expected="<=0.25",observed=tv))

    # Correlation similarity when source statistics provide target coefficient.
    for t in tables:
        spec=specs.get(_key(t.name));
        if not spec:continue
        for corr in spec.correlations:
            if corr.kind!="pearson" or corr.coefficient is None or len(corr.columns)<2:continue
            pairs=[]
            for r in t.rows:
                try:pairs.append((float(r[corr.columns[0]]),float(r[corr.columns[1]])))
                except Exception:pass
            observed=_pearson([p[0] for p in pairs],[p[1] for p in pairs]) if pairs else None
            ok=observed is not None and abs(observed-corr.coefficient)<=corr.tolerance
            checks.append(_pass("correlation_similarity",f"{t.name} {corr.columns}: target={corr.coefficient:.3f}, observed={observed:.3f}",table=t.name,expected=corr.coefficient,observed=observed) if ok else _fail("correlation_similarity",f"{t.name} {corr.columns}: target={corr.coefficient}, observed={observed}",table=t.name,expected=corr.coefficient,observed=observed))
    passed=all(c.passed or c.severity in {"info","warning"} for c in checks); weighted=[c for c in checks if c.severity=="error"]
    score=100.0 if not weighted else round(100*sum(c.passed for c in weighted)/len(weighted),2)
    return ValidationReport(passed=passed,checks=checks,quality_score=score)
