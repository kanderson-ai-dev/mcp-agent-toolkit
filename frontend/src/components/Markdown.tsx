import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Agent output rendered as Markdown (GFM). Links open externally with
 * `noopener noreferrer`; react-markdown sanitizes raw HTML — scripts,
 * `javascript:` hrefs and `onerror` payloads stay inert.
 */
export function Markdown({ children }: { children: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        a: ({ href, children }) => (
          <a href={href} target="_blank" rel="noopener noreferrer">
            {children}
          </a>
        ),
      }}
    >
      {children}
    </ReactMarkdown>
  );
}
