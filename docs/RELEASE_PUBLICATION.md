# Qualified release publication

A successful full release matrix publishes its exact assembled bundle. Internal
pull requests and manual runs with `publish=false` produce a unique GitHub
prerelease named `ci-v<VERSION>-run.<RUN_ID>.<RUN_ATTEMPT>`. These releases use
`--prerelease --latest=false`: the normal release and latest designation stay
unchanged. Package names and embedded versions retain their source version;
the release notes identify the tested revision and workflow evidence.

Tag pushes and manual runs with `publish=true` retain the existing normal
`v<VERSION>` release behavior. Development source versions remain prereleases;
stable normal releases still require the production gate. Candidate publication
requires the same complete matrix, checksums and exact-image evidence. It does
not assert production readiness or universal benchmark superiority.

Candidate tags start with `ci-`, outside the `v*` release trigger, so publication
cannot recursively launch another full matrix even when performed by a maintainer.
Neither normal nor candidate publication moves an existing tag or overwrites a
release. A full rerun gets a new run-attempt suffix.

Fork pull requests keep read-only credentials and downloadable workflow artifacts.
A maintainer can dispatch the revision from a branch in this repository to publish
its candidate. No `pull_request_target` elevation or external write token is used.
All publication follows successful assembly; failed or incomplete matrices cannot
publish. The source checkout in the publishing job is the exact validated commit.

Validation: `python tools/test_release_publication.py` and
`python tools/test_release.py`. The policy contract starts red without the new
module and covers normal/development tags, manual validation, unique run identity,
fork isolation and malformed contexts. Hosted qualification proves actual uploads.
