import { documentLink, type KnowledgeSource } from '@/lib/knowledge';
export default function SourcePanel({
  sources,
}: {
  sources: KnowledgeSource[];
}) {
  if (!sources.length) return null;
  return (
    <section className="knowledge-sources" aria-label="Sources">
      <h3>Sources</h3>
      <p className="helper">
        References identify retrieved passages; check the excerpts against the
        answer.
      </p>
      {sources.map((source, index) => (
        <article className="knowledge-source" key={`${source.id}-${index}`}>
          <div>
            <strong>[{source.citation}]</strong>
            <span className={`source-tag ${source.cited ? 'cited' : ''}`}>
              {source.cited ? 'Cited in answer' : 'Retrieved only'}
            </span>
          </div>
          <a
            href={documentLink(source)}
            target="_blank"
            rel="noopener noreferrer"
          >
            {source.filename} · page {source.page}
          </a>
          <p>{source.text}</p>
        </article>
      ))}
    </section>
  );
}
