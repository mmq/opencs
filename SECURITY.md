# Security policy

OpenCS is a game server meant to run on the open internet, so a way to make it misbehave, crash, leak
data or harm other players' games is worth reporting.

## Reporting a vulnerability

Report it privately, through GitHub's **Report a vulnerability** button on the repository's
[Security tab](https://github.com/mmq/opencs-server/security/advisories/new). Please don't open a public
issue for it.

Say what you did, what happened, and the version (`python -m opencs --version`). A script or a wire
capture that reproduces it helps most. You'll get an answer within a week. Once a fix is released, the
advisory is published, crediting you unless you'd rather not be named.

## Supported versions

Fixes go into the latest release only. Run the newest version; upgrading is in
[`docs/OPERATING.md`](docs/OPERATING.md).

## What counts

In scope: the server in this repository, its default configuration, and the deployment the operator
guide describes (systemd unit, file permissions, admin channel). Examples:

- one client crashing the server, stalling the event loop, or disconnecting other players;
- getting around a connection, login, chat or account limit;
- reading or changing another player's account or progress;
- reaching the admin channel without its token, or getting a secret into the logs.

Out of scope, because the original client or the protocol makes them unavoidable (each is explained in the
threat model, [`docs/robustness.md`](docs/robustness.md#threat-model)):

- passwords travel in plain text: the client cannot encrypt;
- scores, bays, hits and positions are reported by the client and can be cheated;
- account names can be probed at logon;
- floods large enough to saturate the network, which only the host's provider and firewall can stop.

If you find a way to make one of these worse than the threat model says, that is in scope.
