#!/usr/bin/env python3
"""Actions-only synthetic current-merge-ref experiment; stdlib, no Arcade data.

OPERATIONS is a JSON array of {pr, expected_head, style, method,
advance_base_sha?}. Run only in epsalmond/release-gate-sandbox with GH_TOKEN.
Every request and outcome is retained in probe-receipts.json, including errors.
"""
import datetime
import json
import os
import pathlib
import re
import time
import urllib.error
import urllib.parse
import urllib.request

REPO = 'epsalmond/release-gate-sandbox'
API = '/repos/' + REPO
CONTEXTS = ['lint / typecheck / test / build', 'conventional commit title',
            'Release image PR admission', 'Release image merge gate',
            'Release image freshness proof']
SHA = re.compile(r'^[0-9a-f]{40}$')
UUID = re.compile(r'^[0-9a-fA-F-]{32,36}$')


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def subset(body):
    """Keep identities, native outcomes and errors; omit verbose provider data."""
    if isinstance(body, list):
        return [subset(item) for item in body[:100]]
    if not isinstance(body, dict):
        return body
    keys = ('number', 'state', 'sha', 'ref', 'name', 'context', 'status',
            'conclusion', 'id', 'uuid', 'merged', 'merged_at', 'merge_commit_sha',
            'mergeable', 'mergeable_state', 'message', 'errors', 'documentation_url',
            'details', 'object', 'tree', 'parents', 'head', 'base', 'check_runs',
            'total_count', 'login', 'creator', 'app', 'slug', 'full_name', 'html_url', 'url')
    return {key: subset(body[key]) for key in keys if key in body}


class ProbeError(Exception):
    pass


class Client:
    def __init__(self, token):
        self.token = token
        self.receipts = []

    def request(self, endpoint, method='GET', data=None):
        # A fixed API origin and repository scope also protect asynchronous URLs.
        if not endpoint.startswith(API + '/'):
            raise ProbeError('Refused endpoint outside synthetic repository')
        started = now()
        payload = None if data is None else json.dumps(data).encode()
        request = urllib.request.Request(
            'https://api.github.com' + endpoint, data=payload, method=method,
            headers={'Authorization': 'Bearer ' + self.token,
                     'Accept': 'application/vnd.github+json',
                     'Content-Type': 'application/json',
                     'X-GitHub-Api-Version': '2022-11-28',
                     'User-Agent': 'synthetic-current-ref-probe'})
        status = None
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                status = response.status
                raw = response.read().decode('utf-8', errors='replace')
        except urllib.error.HTTPError as error:
            status = error.code
            raw = error.read().decode('utf-8', errors='replace')
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raw = json.dumps({'message': str(error)})
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {'message': raw[:2000]}
        receipt = {'started': started, 'finished': now(), 'endpoint': endpoint,
                   'method': method, 'http_status': status, 'body': subset(body)}
        if data is not None:
            receipt['request'] = data
        self.receipts.append(receipt)
        return status, body

    def get(self, endpoint):
        status, body = self.request(endpoint)
        if status != 200:
            raise ProbeError('Read failed: ' + endpoint + ' HTTP ' + str(status))
        return body


def require(condition, message):
    if not condition:
        raise ProbeError(message)


def validate_operation(operation):
    require(isinstance(operation, dict), 'Operation must be an object')
    require(not (set(operation) - {'pr', 'style', 'method', 'expected_head',
                                  'advance_base_sha', 'repo', 'repository'}),
            'Unknown operation fields')
    for key in ('repo', 'repository'):
        require(operation.get(key, REPO) == REPO, 'Refused other repository')
    require(type(operation.get('pr')) is int and operation['pr'] > 0,
            'pr must be a positive integer')
    require(operation.get('style') in ('fresh-only', 'all-statuses', 'check-run'),
            'Invalid style')
    require(operation.get('method') in ('direct', 'async'), 'Invalid method')
    require(isinstance(operation.get('expected_head'), str) and
            SHA.fullmatch(operation['expected_head']), 'Invalid expected_head')
    if 'advance_base_sha' in operation:
        require(isinstance(operation['advance_base_sha'], str) and
                SHA.fullmatch(operation['advance_base_sha']), 'Invalid advance_base_sha')


def pr_identity(client, number, head):
    pr = client.get(API + '/pulls/' + str(number))
    require(pr.get('state') == 'open' and not pr.get('merged_at'), 'PR is not open')
    require(pr.get('base', {}).get('ref', '').startswith('proof-base-'),
            'Base branch is not a synthetic proof base')
    require(pr.get('base', {}).get('repo', {}).get('full_name') == REPO and
            pr.get('head', {}).get('repo', {}).get('full_name') == REPO,
            'PR must use only synthetic repository refs')
    require(pr.get('head', {}).get('sha') == head, 'PR head differs from expected_head')
    branch = pr['base']['ref']
    base = client.get(API + '/git/ref/heads/' + urllib.parse.quote(branch, safe=''))
    return pr, branch, base['object']['sha']


def head_statuses(client, head):
    """Head work must be finished before taking the merge-ref snapshot.

    Native Actions check runs and commit statuses both satisfy the four existing
    head contexts. A pending/latest failed producer cannot be mistaken for done.
    """
    last = None
    for attempt in range(12):
        statuses = client.get(API + '/commits/' + head + '/statuses?per_page=100')
        runs = client.get(API + '/commits/' + head + '/check-runs?per_page=100')
        latest = {}
        for status in statuses:
            latest.setdefault(status.get('context'), status.get('state') == 'success')
        checks = {}
        for run in runs.get('check_runs', []):
            name = run.get('name')
            if name not in checks or run.get('id', 0) > checks[name].get('id', 0):
                checks[name] = run
        last = {context: {'status': latest.get(context),
                         'check': ({'id': checks[context].get('id'),
                                    'status': checks[context].get('status'),
                                    'conclusion': checks[context].get('conclusion')}
                                   if context in checks else None)}
                for context in CONTEXTS[:-1]}
        if all((context in latest or context in checks) and
               latest.get(context, True) and
               (context not in checks or
                (checks[context].get('status') == 'completed' and
                 checks[context].get('conclusion') == 'success'))
               for context in CONTEXTS[:-1]):
            return last
        if attempt < 11:
            time.sleep(5)
    raise ProbeError('Head contexts not successfully finished: ' + json.dumps(last))


def current_merge(client, number, base, head, tree, wait=True):
    last = None
    for attempt in range(10 if wait else 1):
        status, ref = client.request(API + '/git/ref/pull/' + str(number) + '/merge')
        if status == 200 and ref.get('object', {}).get('sha'):
            commit = client.get(API + '/git/commits/' + ref['object']['sha'])
            last = {'sha': commit['sha'],
                    'parents': [parent['sha'] for parent in commit.get('parents', [])],
                    'tree': commit.get('tree', {}).get('sha')}
            if last['parents'] == [base, head] and last['tree'] == tree:
                return last
        if wait and attempt < 9:
            time.sleep(3)
    raise ProbeError('No current merge ref with exact [base, head] parents and head tree: ' +
                     json.dumps(last))


def publish(client, operation, merge):
    sha = merge['sha']
    if operation['style'] == 'check-run':
        return [client.request(API + '/check-runs', 'POST', {
            'name': CONTEXTS[-1], 'head_sha': sha, 'status': 'completed',
            'conclusion': 'success', 'completed_at': now(),
            'output': {'title': 'Synthetic current merge proof',
                       'summary': 'Synthetic current refs/pull merge identity only.'}})]
    contexts = CONTEXTS if operation['style'] == 'all-statuses' else CONTEXTS[-1:]
    return [client.request(API + '/statuses/' + sha, 'POST', {
        'context': context, 'state': 'success',
        'description': 'Synthetic exact current merge ref proof'}) for context in contexts]


def wait_async(client, endpoint, body):
    uid = body.get('uuid') or (body.get('details') or {}).get('uuid')
    if not uid:
        return {'error': 'Async response had no UUID', 'body': subset(body)}
    require(isinstance(uid, str) and UUID.fullmatch(uid), 'Invalid async UUID')
    for attempt in range(24):
        status, final = client.request(endpoint + '/' + uid)
        if status != 200:
            return {'http_status': status, 'body': subset(final)}
        if final.get('status') not in ('pending', 'queued', 'in_progress'):
            return {'http_status': status, 'body': subset(final)}
        if attempt < 23:
            time.sleep(5)
    return {'error': 'Async merge waiter timed out', 'body': subset(final)}


def after_snapshot(client, number, branch):
    # Preserve partial evidence even if one read fails or the merged PR has no M.
    result = {}
    for name, endpoint in (
        ('pr', API + '/pulls/' + str(number)),
        ('base', API + '/git/ref/heads/' + urllib.parse.quote(branch, safe='')),
        ('merge_ref', API + '/git/ref/pull/' + str(number) + '/merge')):
        status, body = client.request(endpoint)
        result[name] = {'http_status': status, 'body': subset(body)}
        if name in ('base', 'merge_ref') and status == 200:
            sha = body.get('object', {}).get('sha')
            if sha:
                commit_status, commit = client.request(API + '/git/commits/' + sha)
                result[name + '_commit'] = {'http_status': commit_status, 'body': subset(commit)}
    return result


def run_operation(client, operation):
    result = {'operation': operation, 'started': now(), 'publication_attempts': []}
    start = len(client.receipts)
    branch = None
    try:
        validate_operation(operation)
        number, head = operation['pr'], operation['expected_head']
        _, branch, base = pr_identity(client, number, head)
        tree = client.get(API + '/git/commits/' + head)['tree']['sha']
        result.update(base_before=base, head=head, head_tree=tree, base_branch=branch)
        result['head_contexts_finished'] = head_statuses(client, head)
        # Head status completion can cause GitHub to regenerate M even with the
        # same B/H. Resolve M here, rather than reusing an earlier PR snapshot.
        for attempt in range(3):
            _, current_branch, current_base = pr_identity(client, number, head)
            require((current_branch, current_base) == (branch, base), 'Base changed before publication')
            before = current_merge(client, number, base, head, tree)
            publication = {'before': before, 'responses': []}
            result['publication_attempts'].append(publication)
            responses = publish(client, operation, before)
            publication['responses'] = [{'http_status': status, 'body': subset(body)}
                                        for status, body in responses]
            require(all(status == 201 for status, _ in responses), 'Publication failed')
            after = current_merge(client, number, base, head, tree)
            publication['after'] = after
            if after['sha'] == before['sha']:
                result['published_merge'] = after
                break
        else:
            raise ProbeError('Merge ref kept changing during publication')
        _, current_branch, current_base = pr_identity(client, number, head)
        require((current_branch, current_base) == (branch, base), 'Base changed before merge request')
        final_merge = current_merge(client, number, base, head, tree, wait=False)
        result['merge_immediately_before_request'] = final_merge
        require(final_merge['sha'] == result['published_merge']['sha'],
                'Merge ref changed after publication')
        endpoint = API + '/pulls/' + str(number) + (
            '/merge-async' if operation['method'] == 'async' else '/merge')
        data = {'sha': head, 'merge_method': 'squash'}
        if operation['method'] == 'async':
            data.update(merge_action='direct_merge', bypass_rules=False)
        if operation.get('advance_base_sha'):
            advance_status, advance_body = client.request(
                API + '/git/refs/heads/' + urllib.parse.quote(branch, safe=''),
                'PATCH', {'sha': operation['advance_base_sha'], 'force': False})
            result['advance'] = {'http_status': advance_status, 'body': subset(advance_body)}
            require(advance_status == 200, 'Base advance failed')
        # Deliberately no PR/ref reads, waits, or asynchronous status work between
        # the PATCH above and this native merge request.
        status, body = client.request(endpoint, 'PUT', data)
        result['merge_request'] = {'http_status': status, 'body': subset(body)}
        if operation['method'] == 'async' and status in (200, 201, 202):
            result['async_final'] = wait_async(client, endpoint, body)
    except (ProbeError, KeyError, TypeError, ValueError) as error:
        result['error'] = str(error)
    finally:
        if branch:
            result['after'] = after_snapshot(client, operation['pr'], branch)
        result['finished'] = now()
        result['requests'] = client.receipts[start:]
    return result


def main():
    report = {'repository': REPO, 'started': now(), 'operations': []}
    token = os.environ.get('GH_TOKEN', '')
    try:
        require(bool(token), 'GH_TOKEN is required')
        for key in ('GH_REPO', 'GITHUB_REPOSITORY'):
            require(os.environ.get(key, REPO) == REPO, 'Refused other repository')
        operations = json.loads(os.environ.get('OPERATIONS', 'null'))
        require(isinstance(operations, list) and bool(operations), 'OPERATIONS must be a nonempty array')
        # Validate the complete batch before any request, including mutations.
        for operation in operations:
            validate_operation(operation)
        client = Client(token)
        for operation in operations:
            report['operations'].append(run_operation(client, operation))
    except (ProbeError, ValueError, TypeError) as error:
        report['error'] = str(error)
    report['finished'] = now()
    encoded = json.dumps(report, indent=2)
    if token:
        encoded = encoded.replace(token, '[REDACTED]')
    pathlib.Path('probe-receipts.json').write_text(encoded + '\n')
    print(encoded, flush=True)


if __name__ == '__main__':
    main()
