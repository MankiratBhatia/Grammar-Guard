import { describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../api/client";
import { SseParser, streamEvents } from "../api/sse";
import { withHighlight } from "../components/Graph";
import { visible } from "../components/ui";
import { asExpression, formatNames, parseNames } from "../lib/examples";
import { formatBig } from "../pages/Algebra";

describe("SseParser", () => {
  it("parses events with CRLF line endings, as sse-starlette sends them", () => {
    const p = new SseParser();
    const out = p.push('event: token\r\ndata: {"a":1}\r\n\r\nevent: end\r\ndata: {}\r\n\r\n');
    expect(out).toEqual([
      { event: "token", data: { a: 1 } },
      { event: "end", data: {} },
    ]);
  });

  it("reassembles an event split across chunks, even mid-line", () => {
    const p = new SseParser();
    expect(p.push('event: tok')).toEqual([]);
    expect(p.push('en\ndata: {"i"')).toEqual([]);
    expect(p.push(':2}\n')).toEqual([]);
    expect(p.push("\n")).toEqual([{ event: "token", data: { i: 2 } }]);
  });

  it("ignores keep-alive comments and keeps non-JSON data as text", () => {
    const p = new SseParser();
    expect(p.push(": ping\n\n")).toEqual([]);
    expect(p.push("event: note\ndata: hello world\n\n")).toEqual([{ event: "note", data: "hello world" }]);
  });

  it("joins multi-line data", () => {
    expect(new SseParser().push('data: {"a":\ndata: 1}\n\n')).toEqual([{ event: "message", data: { a: 1 } }]);
  });
});

function mockFetch(impl: (url: string, init?: RequestInit) => Response | Promise<Response>) {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => impl(url, init)));
}

describe("api client", () => {
  it("turns the error envelope into an ApiError with code and position", async () => {
    mockFetch(() => new Response(JSON.stringify({ error: { code: "regex_syntax", message: "missing ')'", position: 2 } }), { status: 422 }));
    const err = await api.compile("ab(c").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ code: "regex_syntax", position: 2, status: 422, message: "missing ')'" });
  });

  it("reports an unreachable server in plain words", async () => {
    mockFetch(() => {
      throw new TypeError("Failed to fetch");
    });
    await expect(api.health()).rejects.toMatchObject({ code: "network", message: expect.stringContaining("Cannot reach") });
  });

  it("sends JSON bodies to the versioned path", async () => {
    mockFetch(() => new Response("{}"));
    await api.equivalence({ kind: "regex", text: "a", names: {} }, { kind: "regex", text: "b", names: {} });
    const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/v1/equivalence");
    expect(JSON.parse((init as RequestInit).body as string).b.text).toBe("b");
  });

  it("escapes characters that would corrupt a query string, and the value round-trips", async () => {
    mockFetch(() => new Response("{}"));
    await api.learnSubset("(a|b)*&c");
    const url = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).not.toContain("|");
    expect(url).not.toContain("&");
    expect(url).toContain("%7C");
    expect(url).toContain("%26");
    expect(new URL(url, "http://x").searchParams.get("regex")).toBe("(a|b)*&c");
  });
});

describe("streamEvents", () => {
  it("delivers events from a chunked stream and survives a chunk boundary inside an event", async () => {
    const enc = new TextEncoder();
    const body = new ReadableStream({
      start(c) {
        c.enqueue(enc.encode('event: token\ndata: {"i":0}\n\nevent: to'));
        c.enqueue(enc.encode('ken\ndata: {"i":1}\n\n'));
        c.enqueue(enc.encode("event: end\ndata: {}\n\n"));
        c.close();
      },
    });
    mockFetch(() => new Response(body, { headers: { "content-type": "text/event-stream" } }));
    const seen: unknown[] = [];
    await streamEvents("/x", {}, (e) => seen.push([e.event, e.data]));
    expect(seen).toEqual([["token", { i: 0 }], ["token", { i: 1 }], ["end", {}]]);
  });

  it("rejects with the server's validation error before any streaming starts", async () => {
    mockFetch(() => new Response(JSON.stringify({ error: { code: "limit_exceeded", message: "too long", position: null } }), { status: 422 }));
    await expect(streamEvents("/x", {}, () => undefined)).rejects.toMatchObject({ code: "limit_exceeded", message: "too long" });
  });

  it("returns quietly when aborted", async () => {
    mockFetch(() => {
      throw new DOMException("The operation was aborted.", "AbortError");
    });
    await expect(streamEvents("/x", {}, () => undefined, new AbortController().signal)).resolves.toBeUndefined();
  });
});

describe("helpers", () => {
  it("highlights a state by adding a node statement before the closing brace", () => {
    const out = withHighlight("digraph G {\n  q0 -> q1;\n}", 1);
    expect(out).toContain('q1 [style=filled, fillcolor="#ffd966"]');
    expect(out.trimEnd().endsWith("}")).toBe(true);
    expect(withHighlight("digraph G {}", null)).toBe("digraph G {}");
  });

  it("formats huge string counts without losing digits it claims to show", () => {
    expect(formatBig("1234567")).toBe("1,234,567");
    expect(formatBig("39208959660044736")).toBe("3.92 × 10^16"); // 17 digits would overflow the chart column
    expect(formatBig("3620021311123330915434141960445619177341851877")).toBe("3.62 × 10^45");
  });

  it("makes whitespace and the empty string visible", () => {
    expect(visible("a b\n")).toBe("a␣b↵");
    expect(visible("")).toContain("empty");
  });

  it("wraps a regex as an algebra expression, escaping slashes", () => {
    expect(asExpression({ kind: "regex", text: "a/b", names: {} })).toBe("/a\\/b/");
    expect(asExpression({ kind: "expression", text: "digits & x", names: {} })).toBe("digits & x");
  });

  it("round-trips custom names", () => {
    const names = { code: "[A-Z]{2}", id: "[0-9]+" };
    expect(parseNames(formatNames(names))).toEqual(names);
    expect(parseNames("junk line\n= nothing\nok = [a-z]")).toEqual({ ok: "[a-z]" });
  });
});
