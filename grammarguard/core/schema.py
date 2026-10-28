"""Mini JSON-schema -> LL(1) context-free grammar.

The Phase 4 JSON grammar accepts any document of a fixed shape. Here the shape
comes from the user: a schema is COMPILED into a grammar, which the same table
construction (cfg.py) and PDA (pda.py) then enforce. So the output has the
right keys and value types, not just valid syntax.

Supported schema (a deliberately small subset):
    {"type": "object", "properties": {"k1": S1, "k2": S2, ...}}
          keys appear in exactly this order, all of them (so the grammar is LL(1))
    {"type": "array", "items": S, "minItems": 0 | 1}
    {"type": "string"}                  lowercase letters, as in Phase 4
    {"type": "integer"}                 non-negative, no leading zeros
    {"type": "boolean"}   {"type": "null"}
    {"enum": ["red", "green"]}          words made of [a-z_]; compiled to a trie

Compilation is structural recursion: every schema node becomes one nonterminal
(V1, V2, ...); the shared lexical pieces (Str, Digits) are added once. Because
each production starts with a distinct terminal, the result is LL(1) by
construction, and parse_table() verifies it.
"""

from __future__ import annotations

from typing import Any

from grammarguard.core.cfg import Grammar, Symbol, char_range, lit


class SchemaError(ValueError):
    """The schema is malformed or uses an unsupported feature."""


class _Compiler:
    """Accumulates productions while walking the schema."""

    def __init__(self) -> None:
        """Start with the shared lexical nonterminals."""
        self.productions: dict[str, list[list[Symbol]]] = {
            "Str": [[char_range("a", "z"), "Str"], [lit('"')]],
            "Digits": [[char_range("0", "9"), "Digits"], []],
        }
        self.counter = 0

    def fresh(self, prefix: str = "V") -> str:
        """A new unique nonterminal name."""
        self.counter += 1
        return f"{prefix}{self.counter}"

    @staticmethod
    def word(text: str) -> list[Symbol]:
        """A string as a sequence of single-character terminals."""
        return [lit(c) for c in text]

    def compile(self, schema: Any, path: str = "$") -> str:
        """Compile one schema node; returns the nonterminal that derives its values."""
        if not isinstance(schema, dict):
            raise SchemaError(f"{path}: a schema must be an object")
        if "enum" in schema:
            return self.enum(schema["enum"], path)
        kind = schema.get("type")
        name = self.fresh()
        if kind == "object":
            props = schema.get("properties", {})
            if not isinstance(props, dict):
                raise SchemaError(f"{path}.properties must be an object")
            rhs: list[Symbol] = [lit("{")]
            for i, (key, sub) in enumerate(props.items()):
                if not key or any(c in key for c in '"\\') or not key.isprintable():
                    raise SchemaError(f"{path}: unsupported property name {key!r}")
                if i:
                    rhs.append(lit(","))
                rhs += [lit('"'), *self.word(key), lit('"'), lit(":"), self.compile(sub, f"{path}.{key}")]
            rhs.append(lit("}"))
            self.productions[name] = [rhs]
        elif kind == "array":
            item = self.compile(schema.get("items", {}), f"{path}[]")
            min_items = schema.get("minItems", 0)
            if min_items not in (0, 1):
                raise SchemaError(f"{path}: minItems must be 0 or 1")
            more = self.fresh("More")
            self.productions[more] = [[lit(","), item, more], []]
            # Left-factor the common '[' so the table stays LL(1):  V -> [ Body ;  Body -> ] | Item More ]
            body = self.fresh("Body")
            nonempty: list[Symbol] = [item, more, lit("]")]
            self.productions[body] = [nonempty] if min_items else [[lit("]")], nonempty]
            self.productions[name] = [[lit("["), body]]
        elif kind == "string":
            self.productions[name] = [[lit('"'), "Str"]]
        elif kind == "integer":
            self.productions[name] = [[lit("0")], [char_range("1", "9"), "Digits"]]
        elif kind == "boolean":
            self.productions[name] = [self.word("true"), self.word("false")]
        elif kind == "null":
            self.productions[name] = [self.word("null")]
        else:
            raise SchemaError(f"{path}: unsupported type {kind!r}")
        return name

    def enum(self, words: Any, path: str) -> str:
        """Enum of words -> a trie, one nonterminal per trie node (keeps the grammar LL(1)).

        A node has one production per child letter, plus a closing quote if a word ends there.
        """
        if not isinstance(words, list) or not words or not all(isinstance(w, str) and w for w in words):
            raise SchemaError(f"{path}: enum must be a non-empty list of non-empty strings")
        if not all(all(c.islower() or c == "_" for c in w) and w.isascii() for w in words):
            raise SchemaError(f"{path}: enum words may contain only a-z and _")
        trie: dict[str, Any] = {}
        for w in words:
            node = trie
            for c in w:
                node = node.setdefault(c, {})
            node["$"] = {}  # end of word

        def build(node: dict[str, Any]) -> str:
            """Nonterminal for one trie node."""
            name = self.fresh("E")
            alts: list[list[Symbol]] = []
            for c, child in sorted(node.items()):
                alts.append([lit('"')] if c == "$" else [lit(c), build(child)])
            self.productions[name] = alts
            return name

        top = self.fresh()
        self.productions[top] = [[lit('"'), build(trie)]]
        return top


def schema_to_grammar(schema: Any) -> Grammar:
    """Compile a schema to an LL(1) grammar (raises SchemaError / GrammarError)."""
    compiler = _Compiler()
    root = compiler.compile(schema)
    grammar = Grammar(root, compiler.productions)
    grammar.parse_table()  # raises GrammarError if the construction ever produced a conflict
    return grammar
