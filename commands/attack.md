---
name: attack
description: Spawn a zero-context Sonnet subagent to attack the most recent decision or claim and return a kill list, before the answer stands.
argument-hint: "[target] (optional; defaults to the most recent substantive claim, decision, or answer)"
---

Run the manual "spawn a Sonnet subagent to attack this" loop as a single
command. Follow these steps in order.

## 1. Identify the target

Target is `$ARGUMENTS` if given. Otherwise, target the most recent
substantive claim, decision, or answer produced in this session — not a
status update, not a file listing.

## 2. Write a brief

Write a brief as a quoted artifact, 600 words or fewer, containing:

- the question being decided
- the options that were considered
- the choice that was made
- the reasoning for it
- every piece of evidence for that reasoning, each with a resolvable
  locator (`file:line`, `command` plus the output line it names, or a URL)

Strip authorship. No "we established", no "I", no narrating the session's
history — write it as a standalone document a stranger could attack without
knowing how it was produced. List the two least defensible premises in the
reasoning explicitly. Do not omit evidence that cuts against the choice; an
attacker who is missing the inconvenient evidence cannot find what is wrong
with it.

## 3. Spawn the attacker

Spawn exactly one subagent with the Agent tool: `model: "sonnet"`,
`subagent_type: "general-purpose"`, read-only (it should not edit files).
Give it only the brief from step 2 — no conversation history, no prior
turns, no "we established" framing of any kind. Instruct it to:

- verify every locator in the brief actually resolves to what it claims
- attack the choice and the reasoning, including the two flagged premises
- return a ranked kill list, 700 words or fewer: for each item, the claim,
  a verdict of holds / weakened / killed, the reason, and the cheapest test
  that would settle it if still open

## 4. Verify before accepting

When the subagent returns, verify each `killed` or `weakened` item against
its own locator before accepting it — do not take the subagent's word for a
refutation. A refutation that cites a locator that does not resolve to what
it claims is void and does not count, no matter how the subagent phrased it.

## 5. Report

Report to the user in this shape:

- what was killed (and independently verified)
- what was weakened
- what survived
- what changes now as a result

If a kill is verified, revise the earlier answer and say so plainly — do not
let a correct answer be talked out of standing, and do not let a killed
claim keep standing either.

## Keep it cheap

No transcript dumps into the brief. Quote only the specific lines that are
evidence. The brief and the kill list are the only things that leave this
command; the rest of the session's history stays out of both.
