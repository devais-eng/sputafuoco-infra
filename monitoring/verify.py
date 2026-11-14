#!/usr/bin/env python3
"""Run on sputafuoco; reports metrics, never prints credentials."""
import json,subprocess,urllib.request,urllib.parse,pathlib,base64,os,getpass

def service(name):
 return subprocess.check_output(['kubectl','-n','monitoring','get','svc',name,'-o','jsonpath={.spec.clusterIP}'],text=True)
base='http://'+service('ci-monitoring-prometheus')+':9090'
def get(path):
 return json.load(urllib.request.urlopen(base+path,timeout=20))
targets=get('/api/v1/targets')['data']['activeTargets']
for t in targets:
 print('TARGET',t['labels'].get('job'),t['health'],t['lastError'])
required={'monitoring/arc':3,'node-exporter':1,'kubelet':3,'kube-state-metrics':1,'ci-volume-usage':1}
for job,minimum in required.items():
 assert sum(t['labels'].get('job')==job for t in targets)>=minimum, 'Missing targets: '+job
d=json.loads(pathlib.Path(__file__).with_name('dashboard.json').read_text())
for panel in d['panels']:
 expr=panel['targets'][0]['expr'].replace('$__rate_interval','5m')
 r=get('/api/v1/query?'+urllib.parse.urlencode({'query':expr}))
 assert r['status']=='success',r
 count=len(r['data']['result'])
 print('QUERY',panel['title'],'series=',count)
 if panel['title'] not in ['Spazio allocato per runner e volume','ARC: avvio job p95']:
  assert count>0, 'Missing metric: '+panel['title']
password=os.environ.get('GRAFANA_PASSWORD')
if password is None:
 password=getpass.getpass('Current Grafana admin password: ') if os.isatty(0) else pathlib.Path(__file__).with_name('grafana-admin-password').read_text()
req=urllib.request.Request('http://127.0.0.1:13000/api/dashboards/uid/sputafuoco-ci',headers={'Authorization':'Basic '+base64.b64encode(('admin:'+password).encode()).decode()})
r=json.load(urllib.request.urlopen(req,timeout=20))
assert r['dashboard']['uid']=='sputafuoco-ci'
print('GRAFANA dashboard verified:',r['dashboard']['title'])

assert targets and all(t['health']=='up' for t in targets), 'Unhealthy scrape targets'
