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

export async function streamChat(opts: {
  message: string;
  history: { role: "user" | "assistant"; content: string }[];
  conversationId?: string | null;
  onMeta: (meta: {
    conversation_id: string;
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
    throw new Error(text || `Chat failed (${res.status})`);
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
      try {
        const data = JSON.parse(raw);
        if (eventName === "meta") opts.onMeta(data);
        else if (eventName === "token") opts.onToken(data.text || "");
        else if (eventName === "done") opts.onDone();
      } catch {
        // ignore malformed chunks
      }
      eventName = "message";
    }
  }
}
