# Viva preparation

Short, accurate answers to the questions an examiner is most likely to ask. File names point to where the thing is
implemented; the long versions are in [THEORY.md](THEORY.md).

## The 30-second pitch

> LLMs produce text that *usually* matches a format. GrammarGuard makes it *always* match, using automata we implemented
> ourselves: a regex becomes an ε-NFA (Thompson), a DFA (subset construction) and a minimal DFA (table filling). At every
> step, tokens that would make a match impossible are removed before the model picks one. Because we own the automata, we
> also get things generic decoders don't: combining rules with closure properties, diffing two validators with shortest
> counterexamples, synthesizing test suites with transition coverage, counting and uniformly sampling the language, and
> certificates anyone can re-check without the model.

## A. Core automata

**1. Walk me through regex → DFA.**
Parse to an AST (recursive descent, `+ ? {n,m}` desugared into union/concat/star). Thompson's construction builds an ε-NFA by
structural induction (one start, one accept per fragment). Subset construction makes each DFA state the ε-closure of a set of NFA
states, building only reachable subsets. Table filling merges indistinguishable states into the minimal DFA.
Example: `(a|b)*abb` → 14 NFA states → 6 DFA states → 5 minimal (4 + the dead state).

**2. What is the ε-closure and how do you compute it?**
E(S) = all states reachable from S using only ε-edges, including S itself. Plain graph reachability with an explicit stack
(`nfa.py: epsilon_closure`).

**3. Why can subset construction blow up, and what do you do about it?**
A DFA may need 2ⁿ states: `(a|b)*a(a|b){k}` must remember the last k+1 characters, so it needs 2^(k+1). We only build reachable
subsets, and the API refuses beyond a cap (`StateLimitError`): `(a|b)*a(a|b){20}` is rejected in 0.03 s.

**4. Explain table-filling minimization. Why is the result minimal?**
Mark pairs distinguished by ε (one accepting, one not); then mark {p,q} if some symbol leads them to a marked pair; repeat to a
fixpoint; merge unmarked pairs. By the Myhill–Nerode theorem the unmarked relation is exactly "x ~ y iff no suffix distinguishes
them", and the quotient has one state per class, which is the minimum possible. The minimal DFA is unique up to renaming.

**5. You also have a second minimization algorithm. Why?**
Moore's partition refinement is O(n|Σ|) per round instead of O(n²|Σ|). A combined email policy has ~1,300 states and went from
several seconds to 0.4 s. Table filling is kept for small DFAs and because the Learn page records each round. A test checks both give
identical canonical tables.

**6. What is the dead state, and what is `OTHER`?**
The dead (trap) state is the non-accepting state that loops forever. A language model can emit any character, so we add one extra
symbol `OTHER` = "any character not in Σ"; every state has an `OTHER` edge. For a regex all of them go to the dead state. It is
explicit because **complement needs it**: for `!/a/`, a character outside Σ must lead to an accepting state.

**7. How do you complement a DFA? What must be true?**
Swap accepting and non-accepting states. The DFA must be *complete* (every state has a transition for every symbol, including
`OTHER`); otherwise a string that falls off the automaton would be accepted by neither M nor M'.

**8. Show that regular languages are closed under intersection.**
Product construction: states (p,q), δ((p,q),a) = (δ₁(p,a), δ₂(q,a)), accepting iff both are. Running both automata in parallel accepts
exactly L₁ ∩ L₂. Changing the accepting rule gives union, difference and symmetric difference (`dfa.py: product`).

**9. How do you decide whether two regexes are equivalent, and get a counterexample?**
L₁ = L₂ iff L₁ △ L₂ = ∅. Build the symmetric-difference product and BFS from the start to the nearest accepting state; if none exists
they are equivalent. BFS gives a *shortest* counterexample (`equivalent`).

**10. What is a live state and why does decoding care?**
Live = an accepting state is reachable from it (backward BFS from F). If the output so far reaches a non-live state, no completion
can ever match. Constrained decoding only allows tokens that end in live states, so the output is always a prefix of a valid string.

**11. How do you decide if a language is finite?**
Infinite iff some useful state (reachable and live) is on a cycle: that cycle can be pumped. DFS with white/grey/black colouring
(`counting.py: is_finite`). If finite, no string is longer than |Q|−1.

**12. DFA → regex?**
State elimination (Kleene's theorem). Add a new start and final state; remove states one at a time, replacing p→q→r by
`R(p,r) | R(p,q) R(q,q)* R(q,r)`. The result can be exponentially large, so the API only tries on ≤ 40 states.

**13. How do you count strings and sample uniformly?**
N(q,k) = Σ_a N(δ(q,a),k−1) by dynamic programming. To sample, pick a with probability N(δ(q,a),k−1)/N(q,k); the probability of any
string is then 1/N(start,k) (the ratios telescope), so it is exactly uniform.

## B. Context-free side

**14. Why do you need a PDA for JSON? Why not a DFA?**
Matching nested brackets needs unbounded memory. By the pumping lemma `{ⁿ}ⁿ` is not regular, so no DFA enforces balanced nesting.
A PDA's stack does.

**15. What is LL(1), and how do you check it?**
One symbol of look-ahead picks the production. Compute nullable, FIRST and FOLLOW by fixed-point iteration; the table is
`M[A,c] = α` if c ∈ FIRST(α), or α nullable and c ∈ FOLLOW(A). If a cell gets two productions the grammar is not LL(1);
`parse_table()` raises `GrammarError` listing them.

**16. How does your PDA work and why is it deterministic?**
One state, accept by empty stack. Top is a nonterminal: pop it and push the table's right-hand side (chosen by the next character).
Top is a terminal: match and pop. Each table cell has at most one entry, so there is never a choice.

**17. You said the schema compiler hit an LL(1) conflict. What was it?**
The array rule had `V → [ ] | [ Item … ]`; both alternatives start with `[`. Fixed by left-factoring: `V → [ Body`,
`Body → ] | Item More ]`. The compiler always builds the parse table, so a conflict cannot slip through.

## C. The decoding idea

**18. A model emits tokens; a DFA reads characters. How do you connect them?**
For each live state q and token t, run the DFA over t's characters. If it ends in a live state, t is allowed in q and we record the
next state. At every step the logits of other tokens are set to −∞; end-of-text is allowed only in accepting states.

**19. Why is the output guaranteed to match?**
Induction: every emitted token keeps the state live, so the output is always a prefix of some string in L; EOS is only possible in
an accepting state, so if generation ends with EOS the output is in L. It cannot get stuck because GPT-2's byte-level vocabulary has a
single-character token for every ASCII character, so a live state always has an allowed token.

**20. If it is guaranteed, why does the output sometimes not match?**
If it hits the token limit before finishing it is a valid *prefix* but not a complete match; the UI says so (`stop_reason: max_tokens`).

**21. How does the trie speed things up? Does it always?**
We walk a character trie of the vocabulary (97k nodes) alongside the DFA and prune a whole subtree as soon as the state goes
non-live. Output is identical to the per-token loop. Speed-up varies: 114× for an ISO date, 37× for an integer, 3.8× for usernames,
and **0.6× (slower)** for `!contains(/spam/)` because nearly every token is allowed, so nothing is pruned. I report that exception.

**22. What is the lazy index?**
For large DFAs (a 1,265-state policy took 18.6 s to index eagerly) a generation only visits a few dozen states, so a state's allowed
set is computed the first time generation reaches it.

**23. Why does the PDA decoder check only the top-100 tokens?**
A PDA configuration includes an unbounded stack, so no finite table can be precomputed. We check the model's top-100 tokens
against the current stack, and walk the whole vocabulary only when none fits. Greedy over the top-100 equals greedy over everything
(a valid token inside the top-100 outranks all outside it); with sampling the cut is an approximation.

**24. What is "constraint pressure"?**
`pressure = 1 − p(A)` where A is the allowed set; `cost_bits = −log₂ p(A)`. That is exactly the KL divergence of the constrained
distribution from the model's: KL(q‖p) = −log p(A) for q = p restricted to A. It measures how much the constraint had to override
the model; 0 bits means the model already wanted to comply.

**25. What is a certificate and why trust it?**
The DFA state (or PDA stack) after every character, token boundaries, the constraint text, and a SHA-256 fingerprint of the
automaton. The verifier rebuilds the automaton from the constraint text itself, compares the fingerprint, and replays the run in
linear time. It trusts neither the generator nor the model. "Valid" (truthful) is separate from "accepted" (in the language).

## D. Novelty and honesty

**26. Isn't this just Outlines / llama.cpp grammars?**
The decoding substrate is similar, and I say so. What this adds, all built on our own automata: composing constraints with closure
properties (`email & !contains(/spam/) & length(8,30)`), validator diff with shortest counterexamples, test-suite synthesis with
transition coverage and mutation-tested negatives, exact counting and uniform sampling, DFA → regex explanations, certificates a third
party can check, and a measurable cost metric (bits).

**27. What do the benchmark numbers actually show?** (See the Benchmarks page / `eval/results/latest.md` for the current numbers.)
Validity is judged on the *whole* output by our DFA and cross-checked with Python's `re`. The unconstrained baseline gets few-shot
prompts and a best-of-5 retry baseline, so the comparison is not against a strawman. Intervals are 95% Wilson intervals.

**28. What are the limitations?**
"Any other character" is infinitely many inputs, so test generation tries only a few likely offenders; the PDA's mass metric is a
lower bound after a top-100 check; top-100 sampling is an approximation; JSON is compact (no whitespace) and strings are lowercase
only; the regex dialect has no `.`, anchors, negated classes or `\d`; the model is small (GPT-2), so quality of the *content* is
limited, only the *format* is guaranteed.

**29. What did you not build?**
Chomsky/Greibach normal forms, CYK parsing, pumping-lemma tooling, linear bounded automata, the Post correspondence problem,
nondeterministic PDAs and Turing machines. The grammar is used directly in LL(1) form, which is why only deterministic
context-free languages are supported.

**30. Tell me about a real bug you found.**
`shortest_strings` used a BFS over prefixes. For a dense language such as an email policy (shortest match 8 characters, ~40 symbols)
it queued ~40⁷ prefixes and exhausted memory, crashing my development VM repeatedly. It was exposed when an API endpoint asked for
example strings. The fix is a feasibility table (`can[r]` = states reaching acceptance in exactly r more symbols) plus DFS that only
follows guaranteed branches: 4.5 ms on the same input, with a brute-force comparison test and a timing regression test.

**31. How did you test the automata?**
Differential testing against Python's `re.fullmatch` (and `json.loads` for the JSON PDA) on random strings from Hypothesis, including
near-misses of matching strings, 300 random regexes, and 16 realistic validators (dates, phone numbers, IDs, emails). Algebraic properties are checked by brute force
(complement, product, concat/star, minimization idempotence, equality of the two minimizers, DFA → regex → DFA round trips), and
shortest counterexamples are verified minimal by exhaustive search over shorter strings.

## E. Engineering

**32. How is the API protected from abuse?**
Input-length limits, a cap on automaton size (so subset construction and products cannot blow up), generation time limit and token
limit, a single model lock (CPU GPT-2 is not thread-safe), cancellation when the client disconnects, and one consistent error
envelope with a code and a character position. User mistakes are HTTP 422; only genuine bugs are 500.

**33. How does streaming work?**
Server-Sent Events from a worker thread: the model is blocking CPU code, so each request runs in its own thread feeding an asyncio
queue. If the client disconnects the response is cancelled, a cancel token is set, and the worker stops at the next token and releases
the model lock. The frontend reads the stream with `fetch` because `EventSource` cannot POST.

**34. How do the frontend types stay in sync with the backend?**
`npm run gen:api` dumps the live OpenAPI schema and generates TypeScript types, so a renamed field breaks the frontend build instead of
production.

**35. Why is everything in `core/` standard-library only?**
It is the part being examined: regex parser, NFA, DFA, minimization, product, elimination, counting, CFG and PDA are all hand-written.
`mypy` is enforced on that package. Only the decoding layer imports torch and transformers.
