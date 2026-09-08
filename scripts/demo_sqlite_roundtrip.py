from __future__ import annotations
import json, sqlite3, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from app.connectors import create_connector
from app.loaders import load_table
from app.models import ConnectorConfig, SystemGenerateRequest
from app.system_generator import generate_system

SOURCE_DDL='''
PRAGMA foreign_keys=ON;
CREATE TABLE guests (
 guest_id INTEGER PRIMARY KEY,
 first_name TEXT NOT NULL,
 last_name TEXT NOT NULL,
 email TEXT NOT NULL UNIQUE,
 state TEXT,
 city TEXT
);
CREATE TABLE reservations (
 reservation_id INTEGER PRIMARY KEY,
 guest_id INTEGER NOT NULL REFERENCES guests(guest_id),
 check_in_date TEXT NOT NULL,
 check_out_date TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('confirmed','cancelled','checked_in','completed')),
 nightly_rate REAL NOT NULL CHECK(nightly_rate >= 0)
);
'''

TARGET_DDL=SOURCE_DDL


def seed_source(path:Path)->None:
    con=sqlite3.connect(path); con.executescript(SOURCE_DDL)
    guests=[(1,'Asha','Patel','asha@example.test','MA','Boston'),(2,'Luis','Garcia','luis@example.test','CA','San Diego'),(3,'Mei','Chen','mei@example.test','WA','Seattle')]
    reservations=[(1,1,'2026-09-10','2026-09-13','confirmed',219.0),(2,2,'2026-10-01','2026-10-05','cancelled',310.0),(3,3,'2026-09-20','2026-09-21','completed',179.0)]
    con.executemany('INSERT INTO guests VALUES(?,?,?,?,?,?)',guests); con.executemany('INSERT INTO reservations VALUES(?,?,?,?,?,?)',reservations); con.commit(); con.close()


def main()->int:
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); source=root/'source.db'; target=root/'target.db'; seed_source(source)
        src=create_connector(ConnectorConfig(connector='sqlite',path=str(source),read_only=True))
        try:
            source_tables=src.introspect_system('main')
            profiles={t.name:src.profile_table(t.name,'main',100).model_dump(mode='json') for t in source_tables}
        finally: src.close()
        result=generate_system(SystemGenerateRequest(database_type='sqlite',database_name='synthetic',schema_name='main',default_row_count=25,seed=20260907,scenario='realistic hotel QA data with 12% cancellations',tables=source_tables))
        con=sqlite3.connect(target); con.executescript(TARGET_DDL); con.close()
        target_cfg=ConnectorConfig(connector='sqlite',path=str(target),read_only=False)
        for table in result.tables:
            load_table(target_cfg,table,batch_size=10,mode='append')
        con=sqlite3.connect(target); con.execute('PRAGMA foreign_keys=ON')
        counts={name:con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in ('guests','reservations')}
        bad_fk=con.execute('SELECT COUNT(*) FROM reservations r LEFT JOIN guests g ON r.guest_id=g.guest_id WHERE g.guest_id IS NULL').fetchone()[0]
        source_unchanged=sqlite3.connect(source).execute('SELECT COUNT(*) FROM guests').fetchone()[0]==3
        con.close()
        report={
            'source_tables':[t.name for t in source_tables],
            'profiles_sampled':{k:v['sampled_rows'] for k,v in profiles.items()},
            'generated_rows':sum(len(t.rows) for t in result.tables),
            'validation_passed':result.validation.passed,
            'quality_score':result.validation.quality_score,
            'target_counts':counts,
            'target_bad_foreign_keys':bad_fk,
            'source_unchanged':source_unchanged,
        }
        print(json.dumps(report,indent=2))
        return 0 if result.validation.passed and bad_fk==0 and source_unchanged and counts=={'guests':25,'reservations':25} else 1

if __name__=='__main__': raise SystemExit(main())
