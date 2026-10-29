# Theory behind GrammarGuard

Every algorithm below is implemented from scratch in `grammarguard/core/` using only the Python standard library
(no automata libraries, no `re`). Notation: Σ is an alphabet, δ a transition function, L(M) the language of M.

```
regex ──parse──▶ AST ──Thompson──▶ ε-NFA ──subset construction──▶ DFA ──minimize──▶ minimal DFA
                                                                                       │
        closure operations (∩ ∪ − ¬ · *)  ◀─────────────────────────────────────────────┤
        DFA → regex (state elimination), counting, sampling, test synthesis, equivalence │
                                                                                       ▼
                      token index (DFA × vocabulary trie) ──▶ masked decoding of GPT-2 ──▶ certificate
LL(1) grammar ──▶ parse table ──▶ deterministic PDA ──▶ the same decoding loop, with a stack
```

## 1. Regular expressions → AST (`regex_parser.py`)

A regex over Σ is built from ε, symbols, union `R|S`, concatenation `RS` and star `R*`. Everything else is sugar and is
**desugared while parsing**:

| Sugar | Rewritten as |
|---|---|
| `R+` | `R R*` |
| `R?` | `R | ε` |
| `R{n}` | `R` repeated n times (`R{0}` = ε) |
| `R{n,m}` | `R{n} (R?){m−n}` |
| `R{n,}` | `R{n} R*` |
| `[a-z]` | one leaf holding the finite symbol set (a union of single symbols) |

The parser is recursive descent over an LL(1) grammar: `union → concat ('|' concat)*`, `concat → repeat*`,
`repeat → atom (*|+|?|{n,m})*`, `atom → CHAR | CLASS | '(' union ')'`. Precedence (union < concatenation < postfix)
is encoded by the call chain.

## 2. Thompson's construction (`nfa.py`)

Structural induction on the AST. Each fragment has **one start and one accepting state, and the accepting state has no
outgoing edges**, which is what makes the glue rules safe:

* ε, `a`, `[abc]`: two states, one edge (a class is one edge per symbol).
* `R S`: ε-edge from R's accept to S's start.
* `R | S`: new start with ε to both, both accepts with ε to a new accept.
* `R*`: new start and accept; ε-edges for "skip", "enter", "repeat", "leave".

An NFA with m AST nodes has at most 2m states. `(a|b)*abb` gives **14** states (measured).

`epsilon_closure(S)` is graph reachability over ε-edges only (explicit-stack DFS). The NFA can also be simulated
directly by tracking the set of current states: `E(move(S, a))`.

## 3. Subset construction (`dfa.py`)

Each DFA state is the **ε-closure of a set of NFA states**: q₀' = E({q₀}), δ'(S, a) = E(move(S, a)), and S is accepting
iff it contains an NFA accepting state. Only subsets *reachable* from q₀' are built (worklist), not all 2ⁿ.
The worst case is still exponential: `(a|b)*a(a|b){k}` needs 2^(k+1) states because the DFA must remember the last k+1
characters. A server cannot hang on that, so `subset_construction(max_states=…)` raises `StateLimitError`;
`(a|b)*a(a|b){20}` is refused in 0.03 s instead of building two million states.

## 4. Complete DFAs and the `OTHER` symbol

A language model can emit **any** character, not only those in the regex. All characters outside Σ behave the same, so
the DFA gets one extra symbol, `OTHER` ("any character not in Σ"), with a transition from every state.

For an ordinary regex every `OTHER` edge goes to the dead (trap) state. The symbol is explicit rather than implicit
because **complement needs it**: for `!/a/` a character outside Σ must lead to an *accepting* state. A DFA that left
those edges implicit could not be complemented correctly. Counting and sampling work over Σ only (OTHER stands for
infinitely many characters).

## 4a. Live and dead states

A state is **live** if some accepting state is reachable from it (backward BFS from F). Not live = no completion exists.
This is the notion constrained decoding needs: the output so far must always be a **prefix of some string of L**.
Emptiness of L(M) is "q₀ is not live". **Finiteness**: L is infinite iff some *useful* state (reachable and live) lies on
a cycle (DFS with white/grey/black colouring); a finite language has no string longer than |Q|−1.

## 5. Minimization (`dfa.py`)

By Myhill–Nerode the minimal DFA is unique up to renaming, and its states are the equivalence classes of the relation
"x ~ y iff for every z: xz ∈ L ⇔ yz ∈ L".

* **Table filling**: mark pairs {p,q} with exactly one accepting state (distinguished by ε); repeat: mark {p,q} if some
  symbol a sends them to a marked pair. Unmarked pairs are equivalent. O(n²·|Σ|) per round. Used for small DFAs and for the
  step-by-step Learn page (which records each round).
* **Moore partition refinement**: start from {F, Q∖F}, split blocks whose members disagree on the block reached by some symbol,
  until stable. Used above 64 states (a combined email policy with ~1,300 states went from seconds to 0.4 s).
  Both compute the same classes (a test checks identical canonical tables).

After minimization states are renumbered in BFS order (symbols in sorted order, OTHER last), so a minimal DFA has a
**canonical table**; `signature()` hashes it. Equivalent regexes share a signature, which is why the token-index cache
and the certificates are keyed by it. `(a|b)*abb` minimizes to 4 states + the dead state = **5**.

## 6. Closure properties and equivalence (`dfa.py`, `algebra.py`)

| Operation | Construction |
|---|---|
| `A & B`, `A | B`, `A - B`, `A △ B` | **product DFA**: states (p, q), δ((p,q),a) = (δ₁(p,a), δ₂(q,a)); accepting set depends on the operation. Alphabets are unified; a symbol missing from one side takes its OTHER edge. |
| `!A` | swap accepting and non-accepting (valid because the DFA is complete, including OTHER) |
| `concat(A, B)` | glue NFAs with ε from A's accepting states to B's start, determinize, minimize |
| `star(A)` | new accepting start with ε into A; ε from A's accepting states back; determinize, minimize |

**Equivalence**: L₁ = L₂ iff L₁ △ L₂ = ∅. We build the symmetric-difference product and BFS for the shortest accepted
string, which is therefore a **shortest counterexample** (alphabetically first among the shortest).

The constraint-algebra language (`email & !contains(/spam/) & length(8, 30)`) parses by recursive descent and evaluates
each operator with the construction above, minimizing after every step. `contains(A)` = `ANY* A ANY*`; `length(n,m)` is a
counter automaton.

## 7. DFA → regex: state elimination (`elimination.py`)

Kleene's theorem in the other direction. Add a new start s (ε to q₀) and final f (ε from every accepting state), keep only
useful states, and eliminate states one by one: for every p → q → r add `R(p,r) | R(p,q) R(q,q)* R(q,r)`. What remains is
R(s,f). Edge labels are ASTs combined with simplifying constructors (merge single-symbol alternatives into a class,
`R R*` → `R+`, `a{n}` runs) so results are readable: `(a|b)*` → `[ab]*`. The regex can blow up exponentially, so the API only
tries on automata of ≤ 40 states, and the elimination has a size budget.

## 8. Counting, sampling, shortest strings (`counting.py`)

`N(q, 0) = [q ∈ F]`, `N(q, k) = Σ_a N(δ(q,a), k−1)` counts accepted strings of length k in O(k·|Q|·|Σ|), though the answer
is exponential (a test policy has 3.6 × 10⁴⁵ strings). **Uniform sampling**: from q with k symbols left, pick a with
probability N(δ(q,a),k−1)/N(q,k); the probability of any accepted string is the product of the ratios
= 1/N(start,k), so every string is equally likely (checked statistically in the tests).

`shortest_strings` needs care. A BFS over *prefixes* is exponential: for a dense language whose shortest match has length 8
over a 40-symbol alphabet it queues ~40⁷ prefixes (this exhausted memory and crashed the development VM). The fix uses a
feasibility table `can[r]` = states that reach acceptance in **exactly** r more symbols, and a depth-first search that only
follows branches in `can[...]`, so every branch explored ends in a result (4.5 ms on the same input).

## 9. Test-suite synthesis (`testgen.py`)

A validator is a DFA, so graph-coverage criteria apply. **Positive cases**: for every useful transition p →ᵃ r, the string
`access(p) · a · completion(r)` (shortest access string and shortest completion), giving **100% transition coverage**.
**Negative cases**: *boundary* (valid prefix + the first character that breaks it), *substitution* (a valid string with one
character swapped for a likely-wrong one), *deletion* (a required character left out), *truncation* (stops too early).
Substitution and deletion were added after a mutation test showed that boundary cases alone miss validators that over-accept
a separator or a length. Infinitely many "other characters" cannot all be tried, so a few common offenders are used.

## 10. Context-free grammars, LL(1) and the PDA (`cfg.py`, `pda.py`, `schema.py`)

Grammar symbols are characters (or character classes), so the same automaton can consume text character by character.
`FIRST`, `FOLLOW` and nullability are computed by fixed-point iteration; the LL(1) table is
`M[A, c] = α` if c ∈ FIRST(α), or α is nullable and c ∈ FOLLOW(A). A cell with two productions is a conflict and raises
`GrammarError`.

The **deterministic PDA** has one state and accepts by empty stack. *Expand*: top is nonterminal A, look-ahead c → pop A,
push `M[A,c]` reversed. *Match*: top is a terminal containing c → pop. At end of input only ε-expansions (`M[A, EOF]`)
may remain. Because each table cell has at most one entry, the PDA is deterministic. Stacks are immutable tuples so a
candidate token can be tried without disturbing the real stack.

**Schema compiler**: a mini JSON-schema is compiled by structural recursion into an LL(1) grammar (one nonterminal per
schema node; enums become a trie so common prefixes stay LL(1)). Building the array rule exposed a genuine LL(1) conflict
(`[` `]` versus `[` item…), fixed by **left-factoring** the shared `[`; the compiler always runs the table construction so a
conflict can never slip through. Why a stack is needed: nested brackets `{ { … } }` are not regular (pumping lemma), so no
DFA can enforce them.

## 11. From automata to constrained decoding (`decoding/`)

A model emits **tokens** (multi-character strings); the DFA reads **characters**. For a state q and token t, run the DFA
over t's characters. t is *allowed* in q iff δ\*(q, t) is live.

**Guarantee.** If every emitted token keeps the state live and the end-of-text token is allowed only in accepting states, then
(i) the output is always a prefix of some string in L, and (ii) if generation ends with EOS the output is in L.
*Proof sketch*: induction on tokens; live states only lead to live-or-dead states, and a token ending in a dead state is
never allowed. **No dead end**: a live state has an edge on some symbol into a live state, and GPT-2's byte-level vocabulary
has a single-character token for every ASCII character, so some token is always allowed; in an accepting state EOS is.

**Trie index.** Rather than running the DFA over each of the ~50k tokens, walk a character trie of the vocabulary
(97,317 nodes) together with the DFA. If the DFA state becomes non-live the whole subtree is pruned, which is valid because
non-live is closed under transitions. The output equals the per-token loop (tested on random vocabularies and the real one).
Measured speed-up depends on how restrictive the constraint is: ISO date 114×, integer 37×, username 3.8×, and for
`!contains(/spam/)` (almost every token allowed, nothing pruned) it is *slower*, 0.6×.

**Lazy index.** Large automata (a 1,265-state policy took 18.6 s to index eagerly) only ever visit a few dozen states in one
generation, so rows are computed on first use: a 20-token run took 1.3 s.

**PDA decoding.** A PDA configuration includes an unbounded stack, so no finite table can be precomputed. Each step checks
the model's top-100 tokens against the current stack, and only if none is valid walks the whole vocabulary trie carrying the
stack. Greedy decoding over the top-100 equals greedy over the full vocabulary (any valid token inside the top-100 outranks
everything outside); with sampling the top-100 cut is an approximation.

## 12. Constraint pressure (`generate.py`)

With p the model's next-token distribution and A the allowed set:

* `mass_kept = p(A)`, `pressure = 1 − p(A)`.
* `cost_bits = −log₂ p(A)`.

This is exactly the **KL divergence** of the constrained distribution q = p(· | A) from p: KL(q‖p) = Σ_{t∈A} (p_t/Z) log((p_t/Z)/p_t) = −log Z with Z = p(A). So the cost is the information the constraint adds, and the
sum over a run is the total. 0 bits means the model already wanted to comply. Computed in log space (`logsumexp`) so tiny
masses do not underflow. `preferred_legal` records whether the model's own first choice was allowed ("override" otherwise).

## 13. Certificates (`certificate.py`)

A certificate stores the automaton's run on the output (the DFA state, or PDA stack, after every character), the token
boundaries, the constraint text and a SHA-256 fingerprint of the automaton (the canonical minimal-DFA table or the parse
table). A verifier **rebuilds the automaton from the constraint text itself**, checks the fingerprint, and replays the run one
transition at a time. It never trusts the generator or the model. *Valid* (the certificate is truthful) is separate from
*accepted* (the text is in the language): a certificate can honestly prove a text is **not** accepted. Checking is linear in the
length of the text.

## Complexity summary

| Step | Cost |
|---|---|
| Thompson | O(m) states for m AST nodes |
| Subset construction | O(2ⁿ) worst case, reachable subsets only; capped |
| Table filling / Moore | O(n²|Σ|) per round / O(n|Σ|) per round |
| Product | O(n₁n₂|Σ|) |
| Live states, emptiness, equivalence check | O(n|Σ|) / product size |
| Counting | O(k·n·|Σ|) |
| State elimination | exponential output size possible |
| Token index (eager) | O(|live| × trie nodes visited); lazy: O(states visited × trie nodes visited) |
| One generation step | model forward pass + O(allowed set) for the mask |
