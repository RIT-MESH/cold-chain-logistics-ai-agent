#!/bin/bash
G="http://localhost:8080/grafana"
AUTH="admin:Osakijomo142!"

echo "=== SERVED D target (modern format check) ==="
curl -s -u "$AUTH" "$G/api/dashboards/uid/cold-chain-infra-ai" > /tmp/sD.json
python3 -c "
import json
d=json.load(open('/tmp/sD.json'))['dashboard']
for p in d['panels']:
    if p.get('datasource',{}).get('type')=='cloudwatch':
        t=p['targets'][0]
        print('queryMode:', t.get('queryMode'), '| matchAll:', t.get('matchAll'), '| dimFilters:', len(t.get('dimensionFilters',[])), '| dimensions:', bool(t.get('dimensions')), '| id:', t.get('id'))
        break
"

echo ""
echo "=== Run the EXACT served target for each of the 6 infra panels ==="
python3 -c "
import json
d=json.load(open('/tmp/sD.json'))['dashboard']
targets=[]
for p in d['panels']:
    for t in p.get('targets',[]):
        if t.get('datasource',{}).get('type')=='cloudwatch' and 'metricName' in t:
            targets.append((p['title'], t))
json.dump([t for _,t in targets], open('/tmp/infra_targets.json','w'))
for title, _ in targets: print(' -', title[:55])
"
python3 -c "
import json, subprocess
targets = json.load(open('/tmp/infra_targets.json'))
for t in targets:
    q = {'queries': [t], 'from': 'now-24h', 'to': 'now'}
    open('/tmp/q.json','w').write(json.dumps(q))
    out = subprocess.run(['curl','-s','-u','$AUTH','-X','POST','$G/api/ds/query','-H','Content-Type: application/json','-d','@/tmp/q.json'], capture_output=True, text=True).stdout
    try:
        d = json.loads(out)
        r = d.get('results',{}).get('A',{})
        if r.get('error'): print(t['metricName'], '-> ERROR:', str(r['error'])[:90])
        else:
            f=r.get('frames',[])
            v=f[0]['data']['values'][0] if f and f[0]['data']['values'] else []
            print(t['namespace']+'/'+t['metricName'], '-> OK', len(v), 'datapoints')
    except Exception as e:
        print(t['metricName'], '-> PARSE FAIL', str(e)[:60])
"

echo ""
echo "=== SERVED audit targets (modern check) + run one ==="
curl -s -u "$AUTH" "$G/api/dashboards/uid/cold-chain-agent-audit" > /tmp/sA.json
python3 -c "
import json, subprocess
d=json.load(open('/tmp/sA.json'))['dashboard']
count=0
for p in d['panels']:
    for t in p.get('targets',[]):
        if t.get('datasource',{}).get('type')=='cloudwatch' and 'metricName' in t:
            count+=1
            q = {'queries': [t], 'from': '2026-11-17T00:00:00Z', 'to': 'now'}
            open('/tmp/q.json','w').write(json.dumps(q))
            out = subprocess.run(['curl','-s','-u','$AUTH','-X','POST','$G/api/ds/query','-H','Content-Type: application/json','-d','@/tmp/q.json'], capture_output=True, text=True).stdout
            try:
                dd = json.loads(out)
                r = dd.get('results',{}).get('A',{})
                if r.get('error'): print(t['metricName'], '-> ERROR:', str(r['error'])[:80])
                else:
                    f=r.get('frames',[])
                    v=f[0]['data']['values'][1] if f and len(f[0]['data']['values'])>1 else (f[0]['data']['values'][0] if f else [])
                    print(t['metricName'], '-> OK', len(v), 'datapoints | mode:', t.get('queryMode'))
            except Exception as e: print(t['metricName'], '-> FAIL', str(e)[:60])
print('audit CW targets checked:', count)
"

echo ""
echo "=== B/C regression: variable + one panel each ==="
for U in cold-chain-temp-humidity cold-chain-logistics-custody; do
  echo -n "$U: "
  curl -s -u "$AUTH" "$G/api/dashboards/uid/$U" | python3 -c "
import json,sys
d=json.load(sys.stdin)['dashboard']
v=d['templating']['list']
print('vars', len(v), '| query str:', isinstance(v[0]['query'], str) if v else None, '| malformed:', sum(1 for p in d['panels'] if p.get('type')=='query' and not p.get('title')))
"
done

echo ""
echo "=== containers ==="
docker ps --format '{{.Names}}|{{.Status}}' | sort
