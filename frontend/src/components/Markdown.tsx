"use client";

import { Check, Copy } from "lucide-react";
import { memo, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className="absolute right-2 top-2 rounded-md border border-line bg-panel px-1.5 py-1 text-muted opacity-0 transition group-hover:opacity-100 focus:opacity-100"
      aria-label="Copy code"
      onClick={() => {
        navigator.clipboard?.writeText(text).then(() => {
          setDone(true);
          setTimeout(() => setDone(false), 1200);
        });
      }}
    >
      {done ? <Check size={14} /> : <Copy size={14} />}
    </button>
  );
}

function textOf(node: unknown): string {
  if (typeof node === "string") return node;
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (node && typeof node === "object" && "props" in node) return textOf((node as { props: { children?: unknown } }).props.children);
  return "";
}

export const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <div className="prose-trio">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[[rehypeHighlight, { detect: false, ignoreMissing: true }], rehypeKatex]}
        components={{
          // Links from model output open in a new tab without access to this page.
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noopener noreferrer nofollow">
              {children}
            </a>
          ),
          pre: ({ children }) => (
            <div className="group relative">
              <pre>{children}</pre>
              <CopyButton text={textOf(children)} />
            </div>
          ),
          // No raw HTML is rendered (react-markdown escapes it by default); images from model output are not loaded.
          img: ({ alt }) => <span className="text-muted">[image: {alt}]</span>,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
});
