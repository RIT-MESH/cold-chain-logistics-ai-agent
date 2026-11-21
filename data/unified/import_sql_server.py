#!/usr/bin/env python3
"""Import the unified synthetic dataset into an EMPTY SQL Server database. Requires: pip install pyodbc. Connection string in SQLSERVER_CONNECTION_STRING. No existing tables will be dropped."""
import csv,os,pathlib,pyodbc,sqlite3
ROOT=pathlib.Path(__file__).resolve().parent
CS=os.environ.get('SQLSERVER_CONNECTION_STRING')
if not CS:raise SystemExit('Set SQLSERVER_CONNECTION_STRING first')
# Fail before importing if source relationships are invalid.
with sqlite3.connect(ROOT/'unified_cold_chain.sqlite') as check:
    errors = check.execute('PRAGMA foreign_key_check').fetchall()
    if errors:
        raise SystemExit(f'Unified dataset has foreign-key errors: {errors[:5]}')
conn=pyodbc.connect(CS,autocommit=False)
with conn.cursor() as cursor:
    schema=(ROOT/'sql_server_schema.sql').read_text(encoding='utf-8')
    for statement in schema.split(';'):
        if statement.strip():cursor.execute(statement)
    conn.commit()
    for table in ['vehicles','shipments','sensors','telemetry','custody_handoffs','alerts','route_events']:
        with (ROOT/(table+'.csv')).open(encoding='utf-8',newline='') as f:
            rdr=csv.DictReader(f); cols=rdr.fieldnames
            statement='INSERT INTO [unified].['+table+'] ('+','.join('['+c+']' for c in cols)+') VALUES ('+','.join('?' for c in cols)+')'
            cursor.fast_executemany=True;batch=[];n=0
            for row in rdr:
                batch.append(tuple(None if row[c]=='' else row[c] for c in cols))
                if len(batch)==1000:cursor.executemany(statement,batch);n+=len(batch);batch=[]
            if batch:cursor.executemany(statement,batch);n+=len(batch)
            conn.commit();print(table,n)
print('Import finished; all sources are synthetic test data.')
