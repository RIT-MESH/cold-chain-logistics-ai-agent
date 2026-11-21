"""Unified synthetic cold-chain control center; SQLite demo and SQL Server deployment."""
import os, sqlite3
from pathlib import Path
from urllib.parse import urlsplit
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
ROOT=Path(__file__).resolve().parents[1]
load_dotenv(ROOT/'.env')
st.set_page_config(page_title='Unified Cold-Chain Control Center', page_icon='🧊', layout='wide')
DB=ROOT/'data/unified/unified_cold_chain.sqlite'
def fetch(sql, params=None):
    mode=os.getenv('UNIFIED_DATA_BACKEND','sqlite').lower()
    if mode=='mssql':
        from sqlalchemy import create_engine, text
        from sqlalchemy.engine import URL
        url=URL.create('mssql+pyodbc',username=os.getenv('SQL_UI_USER','USR_COLDCHAIN_UI_RO'),password=os.getenv('SQL_UI_PASSWORD'),host=os.getenv('SQL_SERVER_HOST','localhost'),port=int(os.getenv('SQL_SERVER_PORT','1433')),database=os.getenv('SQL_DATABASE','ColdChainLogistics'),query={'driver':'ODBC Driver 18 for SQL Server','Encrypt':os.getenv('SQL_ENCRYPT','yes'),'TrustServerCertificate':os.getenv('SQL_TRUST_SERVER_CERTIFICATE','no')})
        with create_engine(url,pool_pre_ping=True).connect() as c:
            return pd.read_sql_query(text(sql),c,params=params or {})
    with sqlite3.connect(DB) as c:
        return pd.read_sql_query(sql,c,params=params or {})
# ADR-4 least privilege: application identities hold SELECT on the
# FDE_VIEWS schema ONLY (never on unified.* base tables — the Phase 2
# executed grants are the statement of record). MSSQL mode therefore
# maps every dashboard table to its approved read-only view. Column
# usage verified against scripts/setup_unified_views.sql:
# VW_SHIPMENTS/VW_TELEMETRY/VW_CUSTODY/VW_ALERTS expose every column
# this dashboard selects or filters on (shipments+vehicle_id, telemetry
# sensor/GPS/fuel columns, custody handoff_no, alerts alert_at).
_VIEWS={'shipments':'FDE_VIEWS.VW_SHIPMENTS','telemetry':'FDE_VIEWS.VW_TELEMETRY','custody_handoffs':'FDE_VIEWS.VW_CUSTODY','alerts':'FDE_VIEWS.VW_ALERTS','telemetry_incidents':'FDE_VIEWS.VW_TELEMETRY_INCIDENTS'}
def table(name):
    mode=os.getenv('UNIFIED_DATA_BACKEND','sqlite').lower()
    return _VIEWS[name] if mode=='mssql' else name
def safe_url(name):
    url=os.getenv(name,'').strip(); p=urlsplit(url)
    if p.scheme not in ('http','https') or not p.netloc or p.username or p.password or p.fragment: return None
    if p.scheme=='http' and p.hostname not in ('localhost','127.0.0.1'):return None
    return url
st.sidebar.title('🧊 Unified Cold-Chain')
st.sidebar.caption('2021–2026 | SYNTHETIC TEST DATA')
page=st.sidebar.radio('Workspace',['Overview','Shipments','Temperature & Humidity','Fleet GPS / Fuel / Traffic','Custody & Alerts','Grafana Monitoring','AI Assistant (LibreChat)','Floci Incidents','Data Provenance'])

# ------------------------------------------------------------------ LOGOUT
# REAL session invalidation (not cosmetic): navigating to oauth2-proxy's
# sign_out clears the _oauth2_proxy cookie, then the rd= redirect hits
# Cognito's hosted /logout which terminates the COGNITO session too
# (allowed_logout_urls includes this landing path). After that, / is
# fail-closed (403) until a fresh login — Back/Refresh cannot resurrect
# the session because both cookies are gone.
#
# 2026-11-17: rendered with the NATIVE st.link_button (operator-reported:
# the previous unsafe_allow_html anchor never appeared in the deployed
# browser UI). st.link_button is a first-class Streamlit widget — visible
# on every page, no HTML injection, navigates in the same tab.
def logout_url():
    base=os.getenv('APP_PUBLIC_URL','').rstrip('/')
    if not base:
        return None
    from urllib.parse import quote
    landing=f'{base}/oauth2/start'
    cognito=f"https://coldchain-dev.auth.us-east-1.amazoncognito.com/logout?client_id={os.getenv('OAUTH2_PROXY_CLIENT_ID','')}&logout_uri={quote(landing,safe='')}"
    return f"{base}/oauth2/sign_out?rd={quote(cognito,safe='')}"

st.sidebar.markdown('---')
_l=logout_url()
if _l:
    st.sidebar.link_button('🚪 Logout', _l, use_container_width=True,
                           help='Terminates the OAuth2 Proxy session and the Cognito session (full sign-out).')
elif os.getenv('UNIFIED_DATA_BACKEND','').lower()=='mssql':
    st.sidebar.caption('Logout unavailable (APP_PUBLIC_URL not configured)')
st.title(page)
st.caption('All unified logistics records are SYNTHETIC TEST DATA; simulated routes/fuel and calibrated temperatures are not real vehicle observations.')
try:
    shipments=table('shipments');telemetry=table('telemetry');custody=table('custody_handoffs');alerts=table('alerts')
    if page=='Overview':
        summary=fetch(f'SELECT COUNT(*) AS shipments, COUNT(DISTINCT vehicle_id) AS vehicles FROM {shipments}').iloc[0]
        reading_count=int(fetch(f'SELECT COUNT(*) AS n FROM {telemetry}').iloc[0]['n'])
        alert_count=int(fetch(f'SELECT COUNT(*) AS n FROM {alerts}').iloc[0]['n'])
        cols=st.columns(4)
        for c,(lab,val) in zip(cols,[('Shipments',summary['shipments']),('Vehicles',summary['vehicles']),('Sensor / GPS readings',reading_count),('Alerts',alert_count)]):c.metric(lab,f'{int(val):,}')
        df=fetch(f'SELECT substr(departed_at,1,4) AS year, COUNT(*) AS shipments FROM {shipments} GROUP BY substr(departed_at,1,4) ORDER BY year' if os.getenv('UNIFIED_DATA_BACKEND','sqlite')!='mssql' else f'SELECT LEFT(departed_at,4) AS year, COUNT(*) AS shipments FROM {shipments} GROUP BY LEFT(departed_at,4) ORDER BY year')
        st.subheader('Shipment volume by year');st.bar_chart(df.set_index('year'))
        st.subheader('Delivery outcomes');st.dataframe(fetch(f'SELECT receiver_verdict,COUNT(*) AS total FROM {shipments} GROUP BY receiver_verdict'),hide_index=True)
    elif page=='Shipments':
        ids=fetch(f'SELECT shipment_id FROM {shipments} ORDER BY departed_at DESC')['shipment_id'].tolist()
        pick=st.selectbox('Shipment ID',ids)
        st.dataframe(fetch(f'SELECT * FROM {shipments} WHERE shipment_id=:id',{'id':pick}),hide_index=True)
        st.subheader('Recent recorded readings');st.dataframe(fetch(f'SELECT * FROM {telemetry} WHERE shipment_id=:id ORDER BY recorded_at DESC',{'id':pick}).head(100),hide_index=True)
    elif page=='Temperature & Humidity':
        ids=fetch(f'SELECT DISTINCT shipment_id FROM {telemetry} ORDER BY shipment_id')['shipment_id'].tolist()
        pick=st.selectbox('Shipment',ids)
        df=fetch(f'SELECT recorded_at, sensor_id, temperature_c, humidity_pct FROM {telemetry} WHERE shipment_id=:id ORDER BY recorded_at',{'id':pick})
        if not df.empty:
            df['recorded_at']=pd.to_datetime(df['recorded_at']);df=df.set_index('recorded_at')
            st.subheader('Temperature °C');st.line_chart(df[['temperature_c']])
            st.subheader('Humidity %');st.line_chart(df[['humidity_pct']])
            st.dataframe(df.reset_index(),hide_index=True)
    elif page=='Fleet GPS / Fuel / Traffic':
        ids=fetch(f'SELECT DISTINCT shipment_id FROM {telemetry} ORDER BY shipment_id')['shipment_id'].tolist()
        pick=st.selectbox('Shipment',ids)
        df=fetch(f'SELECT recorded_at,latitude,longitude,speed_kmh,traffic_congestion,fuel_consumption_l_per_100km,fuel_level_pct FROM {telemetry} WHERE shipment_id=:id ORDER BY recorded_at',{'id':pick})
        if not df.empty:
            st.map(df[['latitude','longitude']].dropna());st.dataframe(df,hide_index=True)
            df['recorded_at']=pd.to_datetime(df['recorded_at']);df=df.set_index('recorded_at')
            st.line_chart(df[['speed_kmh','traffic_congestion','fuel_level_pct']])
    elif page=='Custody & Alerts':
        ids=fetch(f'SELECT shipment_id FROM {shipments} ORDER BY departed_at DESC')['shipment_id'].tolist()
        pick=st.selectbox('Shipment',ids)
        st.subheader('Custody handoffs');st.dataframe(fetch(f'SELECT * FROM {custody} WHERE shipment_id=:id ORDER BY handoff_no',{'id':pick}),hide_index=True)
        st.subheader('Alerts');st.dataframe(fetch(f'SELECT * FROM {alerts} WHERE shipment_id=:id ORDER BY alert_at',{'id':pick}),hide_index=True)
    elif page=='Grafana Monitoring':
        url=safe_url('GRAFANA_PUBLIC_DASHBOARD_URL')
        if url:
            st.link_button('Open Grafana in a new tab',url)
            st.components.v1.iframe(url,height=850,scrolling=True)
        else:st.info('Configure GRAFANA_PUBLIC_DASHBOARD_URL with the CloudFront /grafana/ dashboard path; Grafana reads the FDE_VIEWS read-only views via its dedicated USR_GRAFANA_RO identity.')
    elif page=='AI Assistant (LibreChat)':
        url=safe_url('LIBRECHAT_PUBLIC_URL')
        if url:
            st.link_button('Open the AI assistant in a new tab',url)
            st.components.v1.iframe(url,height=850,scrolling=True)
            st.info('LibreChat is SEPARATELY AUTHENTICATED (its own login — 01 §7.4). The SQL agent behind it queries FDE_VIEWS read-only views on SQL Server via the LangGraph gateway; SQLite is dashboard demo mode only.')
        else:st.info('Configure LIBRECHAT_PUBLIC_URL with the CloudFront /chat/ path for the separately authenticated agent UI.')
    elif page=='Floci Incidents':
        st.caption('Floci incident pipeline — S3 → SQS → worker → dbo.TelemetryEvents/Incidents → SNS. Events carry a Provenance column that distinguishes replay/synthetic events; this demo has NO live operational feed.')
        if os.getenv('UNIFIED_DATA_BACKEND','sqlite').lower()=='mssql':
            notifications='FDE_VIEWS.VW_INCIDENT_NOTIFICATIONS'   # Phase 4 derived notification state
            linkcheck='FDE_VIEWS.VW_EVENT_LINK_VALIDATION'          # C4 linkage audit backstop
            events_view='FDE_VIEWS.VW_TELEMETRY_EVENTS'
            # --- KPIs + severity distribution (derived from excursion
            # magnitude — the schema stores ThresholdC, not a label).
            def _q(sql):
                try: return fetch(sql)
                except Exception as exc: st.caption(f'Query unavailable: {str(exc)[:110]}'); return None
            inc=_q(f'SELECT IncidentId,ShipmentId,ObservedAtUtc,TemperatureC,ThresholdC,Provenance,Status,CreatedAtUtc FROM {table("telemetry_incidents")}')
            notif=_q(f'SELECT IncidentId,NotificationState,AttemptCount,LastAttemptAtUtc FROM {notifications}')
            ev=_q(f'SELECT TOP 20 EventId,ShipmentId,ObservedAtUtc,TemperatureC,Provenance,IngestedAtUtc FROM {events_view} ORDER BY IngestedAtUtc DESC')
            lk=_q(f'SELECT COUNT(*) AS n FROM {linkcheck}')
            if inc is not None:
                total=len(inc)
                if notif is not None and not notif.empty:
                    acked=int((notif.NotificationState=='sns_acked').sum()); pend=total-acked
                else:
                    acked=pend=None
                c1,c2,c3,c4=st.columns(4)
                c1.metric('Total incidents',total)
                c2.metric('Open status',int((inc.Status=='OPEN').sum()) if 'Status' in inc else '—')
                c3.metric('SNS-acked notifications',acked if acked is not None else '—')
                c4.metric('Pending notifications',pend if pend is not None else '—')
                if total:
                    import numpy as np
                    exc_=inc.TemperatureC-inc.ThresholdC
                    sev={'low':int((exc_<=2).sum()),'medium':int(((exc_>2)&(exc_<=4)).sum()),'high':int((exc_>4).sum())}
                    st.write('Severity distribution (derived from excursion magnitude above threshold):')
                    st.write({k:f'{v} incident(s)' for k,v in sev.items()})
                if total==0 and (ev is None or ev.empty):
                    st.info('No events ingested yet — the Phase 4 pipeline is not yet activated end to end. Once it runs, events land in dbo.TelemetryEvents and threshold breaches create incidents shown here automatically.')
                # --- Recent incidents with derived notification state
                if total:
                    show=inc.sort_values('ObservedAtUtc',ascending=False).head(20)
                    if notif is not None and not notif.empty:
                        show=show.merge(notif,on='IncidentId',how='left')
                    st.dataframe(show,hide_index=True)
                    st.caption('Provenance=synthetic rows are replay/demo events, not real operational incidents. NotificationState=sns_acked means the SNS TOPIC accepted the message — NOT that email was delivered (blueprint 03 §B4).')
                # --- Event-processing history + C4 linkage audit
                if ev is not None and not ev.empty:
                    st.write('Recent event-processing history (ingested stream — separate from the historical dataset):')
                    st.dataframe(ev,hide_index=True)
                if lk is not None and not lk.empty:
                    fails=int(lk.n.iloc[0])
                    if fails: st.warning(f'{fails} ingested event(s) reference a shipment_id that does NOT exist in the unified dataset (see VW_EVENT_LINK_VALIDATION).')
                    else: st.caption('Linkage validation: every ingested event references a unified shipment (0 failures).')
                # --- Queue / DLQ health — best-effort via the instance role
                # (IMDSv2 hop-limit 2, live-verified 2026-11-16). The UI
                # identity stays SQL read-only; no AWS credentials exist in
                # the container — sqs:GetQueueAttributes on the project
                # queues only, graceful "unavailable" otherwise.
                qh={}
                try:
                    import boto3
                    qh_env={'main':os.getenv('TELEMETRY_QUEUE_URL',''),'dlq':os.getenv('TELEMETRY_DLQ_URL','')}
                    s=boto3.client('sqs',region_name=os.getenv('AWS_REGION','us-east-1'))
                    for k,u in qh_env.items():
                        if u:
                            a=s.get_queue_attributes(QueueUrl=u,AttributeNames=['ApproximateNumberOfMessagesVisible','ApproximateAgeOfOldestMessage'])['Attributes']
                            qh[k]={'depth':int(a['ApproximateNumberOfMessagesVisible']),'oldest_s':int(a['ApproximateAgeOfOldestMessage'])}
                except Exception:
                    qh={}
                if qh:
                    st.write('Event-plane queue health:')
                    st.write({k:{'messages visible':v['depth'],'oldest message (s)':v['oldest_s']} for k,v in qh.items()})
                    if qh.get('dlq',{}).get('depth',0):
                        st.error(f"DLQ holds {qh['dlq']['depth']} message(s) — poison payloads or bad shipment refs need review (worker routes rejects there with a FailureReason attribute).")
                else:
                    st.caption('Queue/DLQ health unavailable (event plane not deployed, or instance role not reachable).')
                iurl=safe_url('GRAFANA_PUBLIC_INCIDENT_URL')
                if iurl:st.link_button('Open the Grafana incident dashboard',iurl)
        else:
            st.info('The incident store is part of the SQL Server deployment (SQLite demo mode has no incident tables). Set UNIFIED_DATA_BACKEND=mssql to view FDE_VIEWS.VW_TELEMETRY_INCIDENTS.')
    else:
        st.markdown('**Synthetic unified simulation dataset** (2021–Oct 2026). Some source values were retained or calibrated; GPS paths, fuel, custody and missing years may be modeled. See data/unified/README.md and DATASET_MANIFEST.json. Historical event timestamps are not live vehicle positions.')
        try:
            st.json(__import__('json').loads((ROOT/'data/unified/DATASET_MANIFEST.json').read_text()))
        except FileNotFoundError:
            st.error(f'Dataset manifest file not found: {ROOT/"data/unified/DATASET_MANIFEST.json"} — check the read-only container volume mount for data/unified/DATASET_MANIFEST.json.')
except Exception as exc:
    st.error(f'Data access failed: {exc}')
    if os.getenv('UNIFIED_DATA_BACKEND','sqlite').lower()=='mssql':
        st.info('SQL Server mode: the dashboard reads the FDE_VIEWS read-only views through the UI identity (USR_COLDCHAIN_UI_RO). If this error persists, check the FDE_VIEWS schema grants — the dashboard never touches unified.* base tables.')
    else:
        st.info('SQLite demo: ensure data/unified/unified_cold_chain.sqlite exists. SQL Server: run unified importer and set UNIFIED_DATA_BACKEND=mssql.')
