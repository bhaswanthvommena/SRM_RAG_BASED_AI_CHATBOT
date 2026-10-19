export type Citation = {
  index: number;
  chunk_id: number;
  url: string;
  title?: string | null;
  doc_type?: string | null;
  modified_at?: string | null;
  is_stale?: boolean;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  intent?: string;
};

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export async function fetchSuggestions(): Promise<string[]> {
  try {
    const res = await fetch(`${API_URL}/suggestions`);
    if (!res.ok) return [];
    const data = await res.json();
    return data.suggestions || [];
  } catch {
    return [];
  }
}

export async function sendFeedback(payload: {
  rating: 1 | -1;
  conversation_id?: string | null;
  comment?: string;
}) {
  await fetch(`${API_URL}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export class ChatStreamError extends Error {
  requestId?: string;
  category?: string;

  constructor(message: string, requestId?: string, category?: string) {
    super(message);
    this.name = "ChatStreamError";
    this.requestId = requestId;
    this.category = category;
  }
}

export async function streamChat(opts: {
  message: string;
  history: { role: "user" | "assistant"; content: string }[];
  conversationId?: string | null;
  onMeta: (meta: {
    conversation_id: string;
    request_id?: string;
    intent?: string;
    rewritten_query?: string;
    citations: Citation[];
  }) => void;
  onToken: (text: string) => void;
  onDone: () => void;
  signal?: AbortSignal;
}) {
  const res = await fetch(`${API_URL}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({
      message: opts.message,
      history: opts.history,
      conversation_id: opts.conversationId,
      stream: true,
    }),
    signal: opts.signal,
  });

  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => "");
    throw errorFromHttp(res.status, text);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let eventName = "message";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n");
    buffer = parts.pop() || "";

    for (const line of parts) {
      if (line.startsWith("event:")) {
        eventName = line.slice(6).trim();
        continue;
      }
      if (!line.startsWith("data:")) continue;
      const raw = line.slice(5).trim();
      if (!raw) continue;
      applySseEvent(eventName, raw, opts);
      eventName = "message";
    }
  }
}

/** Parse one SSE data line. Malformed JSON is ignored. Backend error events propagate. */
export function applySseEvent(
  eventName: string,
  raw: string,
  opts: {
    onMeta: (meta: {
      conversation_id: string;
      request_id?: string;
      intent?: string;
      citations: { index: number; chunk_id: number; url: string }[];
    }) => void;
    onToken: (text: string) => void;
    onDone: () => void;
  }
) {
  let data: {
    text?: string;
    message?: string;
    request_id?: string;
    error?: string;
    conversation_id?: string;
    intent?: string;
    citations?: { index: number; chunk_id: number; url: string }[];
  };
  try {
    data = JSON.parse(raw);
  } catch {
    return;
  }
  if (eventName === "meta") {
    opts.onMeta({
      conversation_id: data.conversation_id || "",
      request_id: data.request_id,
      intent: data.intent,
      citations: data.citations || [],
    });
  } else if (eventName === "token") opts.onToken(data.text || "");
  else if (eventName === "error") {
    throw new ChatStreamError(
      data.message || "Something went wrong.",
      data.request_id,
      data.error
    );
  } else if (eventName === "done") opts.onDone();
}

function errorFromHttp(status: number, text: string): ChatStreamError {
  let message = `Chat failed (${status})`;
  let requestId: string | undefined;
  try {
    const body = JSON.parse(text) as { detail?: unknown; request_id?: unknown };
    if (typeof body.detail === "string" && body.detail.trim()) message = body.detail;
    if (typeof body.request_id === "string") requestId = body.request_id;
  } catch {
    if (text && text.length <= 200 && !text.includes("<")) message = text;
  }
  return new ChatStreamError(message, requestId);
}
