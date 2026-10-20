# Synthetic release freshness experiment

Result: a required context published only to the current test merge commit did
not provide a working replacement for strict base freshness in this fixture.

All source here is synthetic. The test rules required five contexts from GitHub
Actions app 15368, with strict mode disabled and no bypass actors. No production
repositories, credentials, builds or services participated.

On October 19, 2026 (PDT), ordinary stale PRs [#5](https://github.com/epsalmond/release-gate-sandbox/pull/5) and
[#6](https://github.com/epsalmond/release-gate-sandbox/pull/6) merged through the direct and asynchronous APIs respectively.
This established ordinary-PR compatibility with loose checks.

The [current-ref probe](https://github.com/epsalmond/release-gate-sandbox/actions/runs/39364073411) resolved the live
`refs/pull/N/merge` after head checks finished, verified exact parents and tree,
published the required result to that merge SHA, confirmed it was still current,
then requested the native merge. It tried a freshness-only commit status
([#1](https://github.com/epsalmond/release-gate-sandbox/pull/1)), all five commit statuses ([#2](https://github.com/epsalmond/release-gate-sandbox/pull/2)), and a check
run alongside real native head checks ([#9](https://github.com/epsalmond/release-gate-sandbox/pull/9)). Direct merges failed
with HTTP 405; the asynchronous request was accepted but ended in `failed`.
Each reported that the freshness context was expected. Head and base were
unchanged; each attempt ended with a regenerated test merge SHA.

A standard `pull_request` workflow was then tested. Although its execution SHA
was the test merge, GitHub attached its check run to the source head.
[#17](https://github.com/epsalmond/release-gate-sandbox/pull/17) merged while fresh. [#18](https://github.com/epsalmond/release-gate-sandbox/pull/18) also merged after
the base advanced, without rerunning that old test merge job. Its asynchronous
merge ended in `merged` at 8:26:59 p.m. PDT. The resulting commit had the advanced
base as its parent and a tree different from the candidate tree.

The [native workflow run](https://github.com/epsalmond/release-gate-sandbox/actions/runs/39364268345) recorded execution merge
`681c3404c31f769675220ebe3ed962df6c39ff48`, source head
`49f692652019491c55105dce5cde9088b40aec87`, and old base
`784c3d6d89725357bbb2e2369a71204e48fd0e66`.

The base then advanced to `30815b46dbc08de6d7f1f5c1733d73f89e0c0c10`.
Result commit `bfed182bff2567905fe74b1b0585b01ef803848d` has that exact parent.
Its tree is `8f17dba8bb603c5a1a542d4146dce1c125aaf548`, rather than the candidate
tree `d65da93025b140482303308ec64192f3b6ac38b9`.

This rejects the tested shortcut. It is not evidence that every possible
GitHub integration has the same behavior, and it does not authorize relaxing
protection in another repository.

Separate synthetic writer-contract tests rejected ordinary-looking aliases in
both event orders without writing freshness success on their shared source
SHA. Replacement claims invalidated the merge gate before changing readiness,
and delayed terminal writers were refused after the generation changed. Those
contracts passed; they do not repair the failed native enforcement prerequisite.

The [final alias/base-advance probe](https://github.com/epsalmond/release-gate-sandbox/actions/runs/39364516688)
published to the current merge SHA, advanced the base, and immediately requested
the direct or asynchronous merge with no intervening PR/ref read. Both attempts
were blocked and preserved the advanced base. These negative controls do not
rescue the failed fresh positive controls.

Cleanup verified: no open fixture PRs or rulesets remain; all fixture branch refs were removed, main was retained, and all five fixture workflows were disabled. Completed runs and closed PRs remain as evidence.
