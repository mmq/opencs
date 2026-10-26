# 10. Every way to make an account goes through `Identity`

## Context

A first login with an unknown name registers it (II §3), and on a public server that is a way to fill the account table or claim names such as `ADMIN`. A web page and a Chamber command (reached through the `JOINGENIE` guest login) are planned as other ways to register. Each would otherwise grow its own copy of the rules.

## Decision

`Identity.new_account_refusal(name, password)` holds every rule a new account must pass: name characters and length, reserved and guest names, password length, and the `MAX_ACCOUNTS` cap. The first-login path (`Identity.begin`), the operator's `create-account` and any later front end call it, and `Identity.register` makes an account outside a login. How often an address may register is a separate gate (`LoginGate.may_register`), because it depends on who is asking. `registration = "open" | "closed"` in `server.toml` decides only whether a first login registers. The SQLite file is in WAL mode, so a second process (a registration page, a backup) can use it while the server runs.

## Consequences

- A rule added to `new_account_refusal` applies to every way of registering at once.
- The rules apply to new accounts only. An account made before them keeps logging in.
- A web front end in another process writes accounts with the same hash (`identity.hash_password`, the `iterations` it stores) and checks the same rules, by importing `opencs.lobby.identity` or by an admin command. The server sees the new row on the next login.
