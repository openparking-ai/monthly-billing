# Contributing to Open Parking AI

Open Parking AI does not accept outside contributions. Pull requests, issues and comments are limited to the maintainers.

## What gets rejected on sight

**Real personal data, anywhere in the repository.** Fixtures, tests, documents
and examples use invented values — `example.com` addresses, made-up names, plates
that belong to nobody. This applies to git metadata too: commit with a masked
address, not a personal one. The running system stores real vehicle identity;
that is the product, it is governed by retention, and it is a different thing
from what is committed here. Two CI guards enforce it and both ship with a
self-test that proves they can fail.

**Anything that handles a raw card number.** Payments are processor-tokenized,
end to end. If a primary account number can reach a variable in this codebase,
the design is wrong, not the code.

**A dependency on Open Parking AI's platform, or on any hosted service.** This
engine is standalone by definition: it identifies with the internet down, on the
same device or LAN as its consumer. A module that reaches for our platform to do
its job has stopped being standalone, and a remote call on the identification
path is out of the question whoever it is to.

**A name from outside this project.** No product, module or hostname from the
maintainer's other, private software appears here — not in code, a comment, a
document, a test, a fixture, a file's path or a commit message.
`.github/scripts/check-no-sibling-names.js` enforces it in CI.

**A test that has never been seen to fail.** If you add a control, show it
failing when the thing it protects is removed. The boundary guard in the
lane-controller repository ships with planted positive controls as the worked
example.

**A silent guess.** When identification is not confident enough, this engine
emits `outcome: "fallback"` and says so. It does not pick the most likely
answer and present it as a measurement. The same rule covers every field of the
record: anything that was not MEASURED is null, and a plausible value in an
unmeasured field is indistinguishable from a measurement to everything
downstream.

**A consumer-visible change to the record without a `schema_version` bump.**
`docs/CONTRACT.md` is the product's public surface. Adding a field is additive
and does not bump it; removing, renaming or redefining one does.

## Style

Match the code already there — its naming, its comment density, its idioms. A
change that reads like the file it lands in is easier to review than a better
one that does not.

Comments should say why, not what.

---

Built by 72 Knots Method by 72Knots.ai
