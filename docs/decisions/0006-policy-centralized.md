# 6. Policy values centralized in policy.py and config

## Context

The spec leaves many values to the server (spec I §10). Scattered literals would hide which numbers are choices.

## Decision

Every value the server chooses is a named constant in `policy.py` or a field in `config/`, with a comment citing its spec section.

## Consequences

- A reader finds every tunable in two places.
- Changing a rule's number never means editing the rule.
- Reviewers can reject unexplained numbers.
