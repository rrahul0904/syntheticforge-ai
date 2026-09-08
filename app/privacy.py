from __future__ import annotations

import hashlib
import re
from typing import Any
from faker import Faker

from .models import ColumnSpec, SensitivityClass

SENSITIVE_PATTERNS: list[tuple[re.Pattern[str],SensitivityClass,str]]=[
    (re.compile(r"(^|_)(password|passwd|pwd|secret|api_?key|token|private_?key)($|_)",re.I),"secret","secret"),
    (re.compile(r"(^|_)(ssn|social_security|passport|driver_?license|national_?id)($|_)",re.I),"sensitive-pii","identifier"),
    (re.compile(r"(^|_)(credit_?card|card_?number|bank_?account|routing_?number|iban)($|_)",re.I),"sensitive-pii","financial"),
    (re.compile(r"(^|_)(patient|medical_record|mrn|diagnosis|icd|medication|provider_npi)($|_)",re.I),"potential-phi","medical"),
    (re.compile(r"(^|_)(email|phone|address|first_name|last_name|full_name|contact_name|customer_name|guest_name|patient_name|employee_name|dob|birth_date|date_of_birth|ip_address|device_id)($|_)",re.I),"personal","personal"),
]


def classify_column(column:ColumnSpec, sample_values:list[Any]|None=None) -> ColumnSpec:
    c=column.model_copy(deep=True); probe=c.name.lower()
    for pattern,cls,_kind in SENSITIVE_PATTERNS:
        if pattern.search(probe): c.sensitivity=cls; return c
    if sample_values:
        text=[str(v) for v in sample_values if v is not None][:100]
        if any(re.fullmatch(r"\d{3}-\d{2}-\d{4}",v) for v in text): c.sensitivity="sensitive-pii"
        elif any("@" in v and "." in v for v in text): c.sensitivity="personal"
    return c


def classify_columns(columns:list[ColumnSpec],rows:list[dict[str,Any]]|None=None)->list[ColumnSpec]:
    return [classify_column(c,[r.get(c.name) for r in (rows or [])]) for c in columns]


def deterministic_pseudonym(value:Any,namespace:str="syntheticforge",length:int=16)->str:
    return hashlib.sha256(f"{namespace}:{value}".encode()).hexdigest()[:length]


def synthesize_sensitive_value(column:ColumnSpec,row_index:int,seed:int=42,mode:str="fully-synthetic",source_value:Any=None)->Any:
    if column.sensitivity=="non-sensitive": return source_value
    if mode=="deterministic-pseudonym" and source_value is not None:
        token=deterministic_pseudonym(source_value,f"{seed}:{column.name}")
        if "email" in column.name.lower(): return f"user-{token[:10]}@example.test"
        return token
    fake=Faker(); fake.seed_instance(seed+row_index)
    n=column.name.lower()
    if "email" in n: return fake.safe_email()
    if "phone" in n: return fake.phone_number()
    if "address" in n: return fake.street_address()
    if "first_name" in n: return fake.first_name()
    if "last_name" in n: return fake.last_name()
    if "name" in n: return fake.name()
    if any(k in n for k in ["ssn","social_security"]): return fake.ssn()
    if "ip" in n: return fake.ipv4_public()
    if "birth" in n or n=="dob": return fake.date_of_birth(minimum_age=18,maximum_age=90).isoformat()
    if column.sensitivity=="secret": return f"REDACTED-{deterministic_pseudonym(row_index, str(seed), 10)}"
    return f"SYN-{deterministic_pseudonym(f'{column.name}:{row_index}',str(seed),14)}"
