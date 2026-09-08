from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .models import BusinessRuleSpec, TableSpec, ValidationCheck


def infer_business_rules(table:TableSpec)->list[BusinessRuleSpec]:
    names={c.name.lower():c.name for c in table.columns}; rules=list(table.business_rules)
    pairs=[("check_in_date","check_out_date"),("start_date","end_date"),("created_at","updated_at"),("paid_at","refunded_at"),("payment_date","refund_date")]
    existing={r.name for r in rules}
    for a,b in pairs:
        if a in names and b in names:
            name=f"{b}_after_{a}"
            if name not in existing: rules.append(BusinessRuleSpec(name=name,expression=f"{names[b]} >= {names[a]}",source="inferred"))
    if "status" in names and "cancellation_date" in names:
        rules.append(BusinessRuleSpec(name="cancelled_has_cancellation_date",expression=f"if {names['status']} == cancelled then {names['cancellation_date']} is not null",source="inferred"))
    return rules


def _parse_date(v:Any):
    if isinstance(v,(date,datetime)): return v
    if isinstance(v,str):
        try: return datetime.fromisoformat(v.replace("Z","+00:00"))
        except ValueError:
            try: return date.fromisoformat(v[:10])
            except ValueError: return None
    return None


def validate_rule(rule:BusinessRuleSpec,rows:list[dict[str,Any]],table_name:str)->ValidationCheck:
    expr=rule.expression.strip(); bad=0; evaluated=0
    # Intentionally small declarative evaluator: column OP column.
    for op in [">=","<=",">","<","==","!="]:
        if op in expr and not expr.lower().startswith("if "):
            left,right=[x.strip() for x in expr.split(op,1)]
            for row in rows:
                a=row.get(left); b=row.get(right)
                if a is None or b is None: continue
                da,db=_parse_date(a),_parse_date(b)
                aa,bb=(da,db) if da is not None and db is not None else (a,b)
                try:
                    ok={">=":aa>=bb,"<=":aa<=bb,">":aa>bb,"<":aa<bb,"==":aa==bb,"!=":aa!=bb}[op]
                except TypeError: continue
                evaluated+=1; bad+=0 if ok else 1
            return ValidationCheck(name=rule.name,validator="business_rule",passed=bad==0,details=f"{evaluated} row(s) evaluated; {bad} violation(s)",expected=expr,observed={"evaluated":evaluated,"violations":bad},severity=rule.severity,table=table_name,remediation="Adjust generation rule or source constraint" if bad else None)
    low=expr.lower()
    if low.startswith("if ") and " then " in low and " is not null" in low:
        # if status == cancelled then cancellation_date is not null
        cond,consequence=expr[3:].split(" then ",1); left,val=[x.strip() for x in cond.split("==",1)]; target=consequence.lower().replace(" is not null","").strip()
        for row in rows:
            if str(row.get(left,"")).lower()==val.strip("'\"").lower():
                evaluated+=1
                if row.get(target) is None: bad+=1
        return ValidationCheck(name=rule.name,validator="business_rule",passed=bad==0,details=f"{evaluated} applicable row(s); {bad} violation(s)",expected=expr,observed={"applicable":evaluated,"violations":bad},severity=rule.severity,table=table_name,remediation="Populate required dependent field" if bad else None)
    return ValidationCheck(name=rule.name,validator="business_rule",passed=False,details="Rule expression is not supported by the safe evaluator",expected=expr,observed="unsupported",severity="warning",table=table_name,remediation="Use a supported declarative rule")
