import type { Citation } from "@/lib/api";

export function CitationCard({ citation }: { citation: Citation }) {
  return (
    <a
      href={citation.url}
      target="_blank"
      rel="noopener noreferrer"
      className="block rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm transition hover:border-brand-500/40 hover:bg-brand-50/50"
    >
      <div className="flex items-start gap-2.5">
        <span className="mt-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded-md bg-brand-700 px-1.5 text-[11px] font-semibold text-white">
          {citation.index}
        </span>
        <div className="min-w-0">
          <p className="truncate font-medium text-ink">{citation.title || "Source"}</p>
          <p className="truncate text-xs text-slate-500">{citation.url.replace(/^https?:\/\//, "")}</p>
          <div className="mt-1.5 flex flex-wrap gap-1.5 text-[11px] text-slate-500">
            {citation.doc_type && (
              <span className="rounded-md bg-slate-100 px-1.5 py-0.5">{citation.doc_type}</span>
            )}
            {citation.modified_at && (
              <span>as of {new Date(citation.modified_at).toLocaleDateString()}</span>
            )}
            {citation.is_stale && (
              <span className="rounded-md bg-amber-100 px-1.5 py-0.5 text-amber-800">
                may be outdated
              </span>
            )}
          </div>
        </div>
      </div>
    </a>
  );
}
