import { ApiError } from "./client";

export interface SseEvent {
  event: string;
  data: unknown;
}

/** Split an SSE byte stream into events. Handles \r\n, comments (pings) and chunk boundaries mid-event. */
export class SseParser {
  private buffer = "";

  push(chunk: string): SseEvent[] {
    this.buffer += chunk.replace(/\r\n/g, "\n");
    const events: SseEvent[] = [];
    let at: number;
    while ((at = this.buffer.indexOf("\n\n")) >= 0) {
      const block = this.buffer.slice(0, at);
      this.buffer = this.buffer.slice(at + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith(":")) continue; // keep-alive comment
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
      }
      if (!data.length) continue;
      const raw = data.join("\n");
      try {
        events.push({ event, data: JSON.parse(raw) });
      } catch {
        events.push({ event, data: raw });
      }
    }
    return events;
  }
}

/**
 * POST a JSON body and read the response as Server-Sent Events.
 * (EventSource cannot POST, so this uses fetch + a stream reader.)
 * Resolves when the server sends `end` or closes the stream; abort the signal to stop early.
 */
export async function streamEvents(
  url: string,
  body: unknown,
  onEvent: (e: SseEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") return;
    throw new ApiError("Cannot reach the GrammarGuard server. Is it running?", "network", 0);
  }
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`.trim();
    let code = "http";
    let position: number | null = null;
    try {
      const e = (await res.json()).error;
      message = e.message;
      code = e.code;
      position = e.position ?? null;
    } catch {
      /* keep the generic message */
    }
    throw new ApiError(message, code, res.status, position);
  }
  if (!res.body) throw new ApiError("The server sent no stream.", "http", res.status);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      for (const ev of parser.push(decoder.decode(value, { stream: true }))) onEvent(ev);
    }
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") return;
    throw e;
  }
}
