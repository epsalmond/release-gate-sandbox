"""Synthetic-only publisher demonstrating the proposed writer contracts."""
import base64
import json
import os
import urllib.request

REPO='epsalmond/release-gate-sandbox'
PREFIX='repos/'+REPO+'/'
CONTEXT='Release image freshness proof'
RAW='Synthetic accepted candidate'
URL='https://github.com/'+REPO+'/actions/runs/'+os.environ['GITHUB_RUN_ID']

def api(endpoint,method='GET',data=None):
    assert endpoint.startswith(PREFIX)
    req=urllib.request.Request('https://api.github.com/'+endpoint,method=method,data=json.dumps(data).encode() if data is not None else None,headers={'Authorization':'Bearer '+os.environ['GH_TOKEN'],'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=30) as response:return json.load(response)

def latest(sha,context):
    rows=api(PREFIX+'commits/'+sha+'/statuses?per_page=100')
    selected=sorted((r for r in rows if r['context']==context),key=lambda r:r['id'],reverse=True)
    return selected[0] if selected else None

def post(sha,context,state,owner):
    row=api(PREFIX+'statuses/'+sha,'POST',{'context':context,'state':state,'description':'Synthetic hardened fixture '+state,'target_url':owner})
    observed=latest(sha,context)
    assert observed['id']==row['id'] and observed['state']==state and observed['target_url']==owner
    return row

def pull(number):
    value=api(PREFIX+'pulls/'+str(number))
    assert value['state']=='open' and value['base']['ref'].startswith('proof-base-')
    return value

def version(sha):
    content=api(PREFIX+'contents/.release-version.json?ref='+sha)
    return json.loads(base64.b64decode(content['content']))['.']

def source_is_release(pr):
    compare=api(PREFIX+'compare/'+pr['base']['sha']+'...'+pr['head']['sha'])
    return version(compare['merge_base_commit']['sha'])!=version(pr['head']['sha'])

def synthetic(pr):
    ref=api(PREFIX+'git/ref/pull/'+str(pr['number'])+'/merge')
    commit=api(PREFIX+'git/commits/'+ref['object']['sha'])
    assert [p['sha'] for p in commit['parents']]==[pr['base']['sha'],pr['head']['sha']]
    head=api(PREFIX+'git/commits/'+pr['head']['sha'])
    assert commit['tree']['sha']==head['tree']['sha']
    return commit['sha']

def run(operation):
    pr=pull(operation['pr'])
    release=source_is_release(pr)
    mode=operation['op']
    if mode=='admit':
        if release:
            # A structurally release-bearing source must never receive success
            # on H, regardless of branch/title or which PR event arrives first.
            if not pr['head']['ref'].startswith('proof-release-'):
                return {'op':mode,'pr':pr['number'],'admitted':False,'release':True,'source_success_written':False}
            return {'op':mode,'pr':pr['number'],'admitted':True,'release':True,'source_success_written':False}
        post(pr['head']['sha'],CONTEXT,'success',URL+'#ordinary/'+str(pr['number']))
        return {'op':mode,'pr':pr['number'],'admitted':True,'release':False,'source_success_written':True}
    assert release and pr['head']['ref'].startswith('proof-release-')
    m=synthetic(pr)
    h=pr['head']['sha']
    if mode=='accept-initial':
        # Synthetic artifact acceptance; no build or real artifact is claimed.
        pending=post(h,RAW,'pending',URL+'#synthetic-claim')
        accepted=post(h,RAW,'success',URL+'#synthetic-accepted/'+str(pending['id']))
        return {'op':mode,'pr':pr['number'],'accepted_id':accepted['id'],'merge':m,'head':h}
    if mode=='begin':
        accepted=latest(h,RAW)
        assert accepted and accepted['state']=='success'
        owner=URL+'#gate/acceptance/'+str(accepted['id'])
        pending=post(m,CONTEXT,'pending',owner)
        return {'op':mode,'pr':pr['number'],'head':h,'merge':m,'accepted_id':accepted['id'],'owner':owner,'gate_status_id':pending['id']}
    if mode=='claim-replacement':
        # This workflow uses one shared concurrency lock for all fixture writers.
        # Invalidate/confirm M before the source readiness mutation.
        invalidated=post(m,CONTEXT,'pending',URL+'#replacement')
        claim=post(h,RAW,'pending',URL+'#replacement/after-gate/'+str(invalidated['id']))
        return {'op':mode,'pr':pr['number'],'head':h,'merge':m,'gate_status_id':invalidated['id'],'claim_id':claim['id']}
    if mode=='finish':
        accepted=latest(h,RAW)
        owned=latest(m,CONTEXT)
        intent=operation['intent']
        valid=accepted and accepted['state']=='success' and accepted['id']==intent['accepted_id'] and owned and owned['state']=='pending' and owned['id']==intent['gate_status_id'] and owned['target_url']==intent['owner'] and m==intent['merge'] and h==intent['head']
        if not valid:return {'op':mode,'pr':pr['number'],'published':False,'reason':'replacement generation or merge identity changed'}
        # Recheck moving inputs immediately before publication while holding the
        # same lock. GitHub native evaluation supplies the actual merge barrier.
        fresh=pull(pr['number'])
        assert fresh['head']['sha']==h and synthetic(fresh)==m
        completed=post(m,CONTEXT,'success',intent['owner'])
        return {'op':mode,'pr':pr['number'],'published':True,'status_id':completed['id'],'merge':m}
    raise ValueError('Unknown fixture operation')

assert os.environ['GITHUB_REPOSITORY']==REPO
receipts=[]
for operation in json.loads(os.environ['OPERATIONS']):
    receipt=run(operation);receipts.append(receipt);print(json.dumps(receipt),flush=True)
with open('hardened-receipts.json','w') as output:json.dump(receipts,output,indent=2)
