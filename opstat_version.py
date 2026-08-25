#!/usr/bin/env python3
"""The one authoritative opstat version.

Every runtime, build and documentation surface derives from this constant or
is test-enforced against it (tests/test_version_contract.py). Before this
module existed the string lived in six places - the `opstat` entrypoint and
all five protocol engines - which agreed only by luck, while three engines'
own header comments still said 0.1.1.

Import-safe by construction: no imports, no side effects, nothing to execute.
That matters because the entrypoint is an extensionless script, so build and
test tooling cannot import it to read a version without running it.

Changing a release version means changing THIS LINE and nothing else; the
release workflow refuses to publish when the pushed tag disagrees
(scripts/check_version_contract.py).
"""

VERSION = "0.1.2"
