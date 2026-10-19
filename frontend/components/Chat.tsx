"use client";

import Image from "next/image";
import { FormEvent, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CitationCard } from "@/components/CitationCard";
import {
  ChatMessage,
  ChatStreamError,
  Citation,
  fetchSuggestions,
  sendFeedback,
  streamChat,
} from "@/lib/api";

function uid() {
  return Math.random().toString(36).slice(2);
}

/** Turn [1], [1][2], [1, 2] into hash links (custom schemes get stripped by react-markdown). */
function citeMarkup(text: string) {
  return text.replace(/\[(\d+(?:\s*,\s*\d+)*)\]/g, (_full, group: string) =>
    group
      .split(/\s*,\s*/)
      .map((n) => `[${n}](#cite-${n})`)
      .join("")
  );
}

function CitationChip({ n, url }: { n: string; url?: string }) {
  const className =
    "mx-0.5 rounded bg-brand-100 px-1 text-[10px] font-semibold text-brand-700 no-underline hover:bg-brand-200";
  if (url) {
    return (
      <sup>
        <a href={url} target="_blank" rel="noopener noreferrer" className={className} title={url}>
          {n}
        </a>
      </sup>
    );
  }
  return <sup className={className}>{n}</sup>;
}

function renderAnswer(text: string, citations?: Citation[]) {
  const byIndex = new Map(
    (citations || []).map((c) => [String(c.index), c.url] as const)
  );
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        a: ({ href, children }) => {
          const cite = href?.match(/^#cite-(\d+)$/);
          if (cite) {
            return <CitationChip n={cite[1]} url={byIndex.get(cite[1])} />;
          }
          if (!href || href.startsWith("#")) {
            return <span>{children}</span>;
          }
          return (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          );
        },
      }}
    >
      {citeMarkup(text)}
    </ReactMarkdown>
  );
}

function uniqueCitations(citations: Citation[] | undefined) {
  if (!citations?.length) return [];
  const seen = new Set<string>();
  return citations.filter((c) => {
    const key = (c.url || "").replace(/\/$/, "").toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function SourcesPanel({
  messageId,
  citations,
  conversationId,
}: {
  messageId: string;
  citations: Citation[];
  conversationId: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [rating, setRating] = useState<1 | -1 | null>(null);
  const items = uniqueCitations(citations);
  if (!items.length) return null;

  async function onFeedback(value: 1 | -1) {
    setRating(value);
    try {
      await sendFeedback({ rating: value, conversation_id: conversationId });
    } catch {
      // keep highlight; network failures are silent
    }
  }

  return (
    <div className="mt-4 border-t border-slate-100 pt-3">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 transition hover:border-brand-500/30 hover:bg-brand-50 hover:text-brand-800"
        aria-expanded={open}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden>
          <path
            d="M7 8h10M7 12h10M7 16h6"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
          />
        </svg>
        Sources ({items.length})
        <span className="text-slate-400">{open ? "▴" : "▾"}</span>
      </button>
      {open && (
        <div className="mt-3 grid gap-2">
          {items.map((c) => (
            <CitationCard key={`${messageId}-${c.index}`} citation={c} />
          ))}
        </div>
      )}
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          aria-pressed={rating === 1}
          className={`rounded-lg px-2.5 py-1 text-xs font-medium transition ${
            rating === 1
              ? "bg-brand-700 text-white"
              : "text-slate-500 hover:bg-slate-100 hover:text-slate-700"
          }`}
          onClick={() => void onFeedback(1)}
        >
          Helpful
        </button>
        <button
          type="button"
          aria-pressed={rating === -1}
          className={`rounded-lg px-2.5 py-1 text-xs font-medium transition ${
            rating === -1
              ? "bg-slate-700 text-white"
              : "text-slate-500 hover:bg-slate-100 hover:text-slate-700"
          }`}
          onClick={() => void onFeedback(-1)}
        >
          Not helpful
        </button>
      </div>
    </div>
  );
}

function SendIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M5 12h14M13 6l6 6-6 6"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

const DEFAULT_SUGGESTIONS = [
  "What is the B.Tech tuition fee?",
  "How do I apply via SRMJEEE?",
  "Hostel facilities on campus",
  "Recent placement statistics",
];

export function Chat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [threadHeight, setThreadHeight] = useState(0);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const turnAnchorRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    fetchSuggestions().then(setSuggestions);
  }, []);

  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    const measure = () => setThreadHeight(el.clientHeight);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const lastUserId = (() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i].role === "user") return messages[i].id;
    }
    return undefined;
  })();

  // Pin the new question to the top once. Do not follow the growing answer.
  useEffect(() => {
    const scroller = scrollerRef.current;
    const anchor = turnAnchorRef.current;
    if (!scroller || !anchor || !lastUserId) return;
    const delta = anchor.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
    scroller.scrollTo({ top: scroller.scrollTop + delta, behavior: "smooth" });
  }, [lastUserId]);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [input]);

  async function ask(question: string) {
    const q = question.trim();
    if (!q || busy) return;

    const userMsg: ChatMessage = { id: uid(), role: "user", content: q };
    const assistantId = uid();
    setMessages((prev) => [...prev, userMsg, { id: assistantId, role: "assistant", content: "" }]);
    setInput("");
    setBusy(true);

    const history = messages.map((m) => ({ role: m.role, content: m.content }));
    abortRef.current = new AbortController();
    let citations: Citation[] = [];

    try {
      await streamChat({
        message: q,
        history,
        conversationId,
        signal: abortRef.current.signal,
        onMeta: (meta) => {
          setConversationId(meta.conversation_id);
          citations = meta.citations || [];
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId ? { ...m, citations, intent: meta.intent } : m
            )
          );
        },
        onToken: (text) => {
          setMessages((prev) =>
            prev.map((m) => (m.id === assistantId ? { ...m, content: m.content + text } : m))
          );
        },
        onDone: () => {},
      });
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") return;
      const msg = err instanceof Error ? err.message : "Something went wrong";
      const streamed = err instanceof ChatStreamError;
      const reference = streamed && err.requestId ? ` Reference: ${err.requestId}` : "";
      setMessages((prev) =>
        prev.map((m) => {
          if (m.id !== assistantId) return m;
          if (streamed) {
            const notice = `${msg}${reference}`;
            return { ...m, content: m.content ? `${m.content}\n\n${notice}` : notice };
          }
          return {
            ...m,
            content:
              m.content ||
              `Sorry — ${msg}. Is the API running on ${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}?`,
          };
        })
      );
    } finally {
      setBusy(false);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void ask(input);
  }

  const showEmpty = messages.length === 0;
  const chips = suggestions.length ? suggestions.slice(0, 4) : DEFAULT_SUGGESTIONS;
  const turns: { user: ChatMessage; assistant?: ChatMessage }[] = [];
  for (const m of messages) {
    if (m.role === "user") turns.push({ user: m });
    else if (turns.length) turns[turns.length - 1].assistant = m;
  }

  return (
    <div className="flex h-full w-full flex-col">
      <header className="sticky top-0 z-20 border-b border-slate-200/80 bg-white/90 backdrop-blur-md">
        <div className="mx-auto flex h-14 max-w-chat items-center px-4 sm:px-6">
          <div className="flex items-center gap-3">
            <Image
              src="/brand/srmap-icon.png"
              alt="SRM University-AP"
              width={36}
              height={36}
              className="h-9 w-9 object-contain"
              priority
            />
            <div className="leading-tight">
              <p className="text-sm font-semibold tracking-tight text-ink">Campus Assistant</p>
              <p className="text-[11px] font-medium text-slate-500">SRM University-AP</p>
            </div>
          </div>
        </div>
      </header>

      <div ref={scrollerRef} className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-chat px-4 py-6 sm:px-6 sm:py-8">
          {showEmpty && (
            <div className="animate-fade-up flex min-h-[min(62vh,560px)] flex-col items-center justify-center text-center">
              <Image
                src="/brand/srmap-logo.png"
                alt="SRM University-AP"
                width={220}
                height={80}
                className="mb-6 h-16 w-auto object-contain sm:h-[4.5rem]"
                priority
              />
              <h1 className="text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
                How can I help you today?
              </h1>
              <p className="mt-2 max-w-md text-sm text-slate-500">
                Ask about admissions, fees, programmes, faculty, hostel, or placements — answers
                cite official srmap.edu.in pages.
              </p>
              <div className="mt-8 grid w-full gap-2.5 sm:grid-cols-2">
                {chips.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => void ask(s)}
                    className="rounded-2xl border border-slate-200 bg-white px-4 py-3.5 text-left text-sm text-slate-700 shadow-sm transition hover:-translate-y-0.5 hover:border-brand-500/35 hover:shadow-soft"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="space-y-6">
            {turns.map((turn, turnIdx) => {
              const isLastTurn = turnIdx === turns.length - 1;
              const assistant = turn.assistant;
              return (
                <div
                  key={turn.user.id}
                  ref={isLastTurn ? turnAnchorRef : undefined}
                  className="space-y-6"
                  style={isLastTurn && threadHeight ? { minHeight: threadHeight } : undefined}
                >
                  <div className="animate-fade-up flex justify-end">
                    <div className="max-w-[85%] rounded-2xl rounded-br-md bg-brand-700 px-4 py-3 text-[15px] leading-relaxed text-white shadow-sm sm:max-w-[75%]">
                      {turn.user.content}
                    </div>
                  </div>
                  {assistant && (
                    <div className="animate-fade-up flex gap-3">
                      <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
                        <Image
                          src="/brand/srmap-icon.png"
                          alt=""
                          width={22}
                          height={22}
                          className="h-5 w-5 object-contain"
                        />
                      </div>
                      <div className="min-w-0 flex-1 pt-0.5">
                        <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">
                          Campus Assistant
                        </p>
                        {!assistant.content && busy && isLastTurn ? (
                          <div className="flex items-center gap-1.5 py-2">
                            <span className="typing-dot h-2 w-2 rounded-full bg-slate-400" />
                            <span className="typing-dot h-2 w-2 rounded-full bg-slate-400" />
                            <span className="typing-dot h-2 w-2 rounded-full bg-slate-400" />
                          </div>
                        ) : (
                          <div className="prose-answer">
                            {renderAnswer(assistant.content, assistant.citations)}
                          </div>
                        )}
                        {assistant.content && assistant.citations && assistant.citations.length > 0 && (
                          <SourcesPanel
                            messageId={assistant.id}
                            citations={assistant.citations}
                            conversationId={conversationId}
                          />
                        )}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <div className="border-t border-slate-200/80 bg-gradient-to-t from-canvas via-canvas to-transparent pb-4 pt-3">
        <form onSubmit={onSubmit} className="mx-auto max-w-chat px-4 sm:px-6">
          <div className="composer-glow flex items-end gap-2 rounded-2xl border border-slate-200 bg-white p-2 shadow-composer transition">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              rows={1}
              placeholder="Message Campus Assistant…"
              className="composer-input max-h-40 flex-1 resize-none overflow-y-auto bg-transparent px-3 py-2.5 text-[15px] leading-relaxed text-ink outline-none placeholder:text-slate-400"
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void ask(input);
                }
              }}
            />
            <button
              type="submit"
              disabled={busy || !input.trim()}
              aria-label="Send message"
              className="mb-0.5 flex h-10 w-10 items-center justify-center rounded-xl bg-brand-700 text-white transition hover:bg-brand-900 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-400"
            >
              <SendIcon />
            </button>
          </div>
          <p className="mt-2.5 text-center text-[11px] text-slate-400">
            Grounded on crawled srmap.edu.in pages. Always verify fees and deadlines on the official
            site.
          </p>
        </form>
      </div>
    </div>
  );
}
