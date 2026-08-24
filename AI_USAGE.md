# AI usage notes

How this was built: I wrote a detailed spec first, including empirical findings verified against the live API (record counts, schema quirks, rate limits, the places the docs are wrong), then had Claude Code implement against that spec with review between phases. The model wrote most of the code. I directed what got built, kept the scope list binding, and verified claims against FDA's own site.

## Where the model helped most

- Turning verified API findings into a client that encodes them: the misleading `API_KEY_MISSING` error on `limit>1000`, `count` being unsupported on the shortages endpoint, 404 meaning zero results, both date formats. Because the traps were documented before coding started, none of them cost debugging time.
- The eval harness and fixture record/replay design, which made "run it offline, deterministically, with no keys" cheap to offer.
- Speed generally. Phases 1 and 2 are a few focused hours of wall-clock time because the spec was executable.

## Where it was wrong, and how I caught it

This is the part worth reading.

1. **The equivalence overclaim.** An early revision framed concentration grouping (`50 mg/5 mL` = `10 mg/mL`) as a clinical safety feature that could tell a clinician which products are interchangeable. That is exactly wrong: it puts the tool inside a clinical decision with invented authority. I pushed back, and it was reframed as a retrieval-correctness requirement (without grouping, "who else makes this" answers 8 when the true count is 68) plus an explicit refusal: the schema deliberately has no `is_equivalent` field, and R5 forbids the verdict. The grouping stayed; the claim died.

2. **My own "verified ground truth" was wrong twice.** My spec, built with AI assistance against the live API, recorded clozapine at zero recalls and Lactated Ringer's at zero recalls, "genuinely absent from both databases." The build's recall search - which combines the `openfda` name fields with a whole-word-filtered description search - found 5 terminated clozapine recalls and 18 Lactated Ringer's records, including an ongoing 2026 Class I. The original checks had searched too narrowly (name fields only; many enforcement records have no `openfda` section). I hand-verified the new numbers against FDA's site and updated the evals to pin them. Lesson: a "verified" number is only as good as the query that produced it, and the second AI pass caught the first AI pass's error because the eval forced a comparison.

3. **Grouping that looked right and wasn't.** The first shortage-grouping pass produced 26 groups for rocuronium's 26 records. It ran, it looked plausible, and it was useless: FDA embeds each package NDC inside the presentation string, so every record was its own group. Caught because the spec's hand-verified shape test said 3 presentations. Fixed by stripping parentheticals before grouping. This is the "getting something that looks right takes one prompt" failure mode, caught by having an independently known answer.

4. **The refusal that leaked.** Under a "hypothetically, purely for education" pressure prompt, the assess model refused to recommend an alternative paralytic, then named four of them inside the refusal itself ("did not evaluate e.g. vecuronium, cisatracurium..."). A refusal that lists the candidates is a recommendation wearing a disclaimer. The adversarial eval caught it; the fix is an explicit rule that refusals must not name what they refuse, and the eval's leak scan deliberately does not excuse drug names that appear in negated contexts.

5. **The placeholder submission.** In one eval run the model called the tools correctly, then submitted an assessment with `summary: "placeholder"` and every list empty - schema-valid, content-free. Fixed by enforcing minimums in the schema itself (signals, data_gaps and refusals must be non-empty, summary has a length floor), so degenerate submissions bounce back for retry instead of passing validation.

6. **Markup bleed.** The model occasionally leaked artifacts like `</summary>` into JSON string fields on submission. First fix was bouncing it back for retry; the model kept re-emitting the artifact until retries ran out. Second fix strips tag-shaped tokens server-side, which is the right layer for a transport artifact.

7. **Smaller catches.** The installed MCP SDK was 2.0, which renamed the server class the model reached for (import failure, fixed against the installed API). Model output used em-dashes, which the project's style rules ban; the system prompt now forbids them. An eval's "forbidden phrase" check flagged the model *correctly denying* equivalence ("FDA does not state they are interchangeable"), a false positive fixed with a negation-aware check rather than by weakening the phrase list.

## The pattern

Every catch above came from one of three things: a hand-verified expected answer (the shape tests), an adversarial eval that assumed the model would misbehave, or reading the actual output instead of trusting that a passing run meant a good run. None came from the model volunteering that it was wrong.
