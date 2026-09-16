"use client";

import { Fragment } from "react";

/**
 * The smallest markdown that makes an answer readable.
 *
 * Everything is built as React elements — never `dangerouslySetInnerHTML`.
 * Model output is untrusted text by definition, and the moment it can carry
 * markup it can carry a link somewhere it should not go. Bold, italics,
 * inline code, bullets, numbers and headings cover what the assistant
 * actually produces; anything else renders as the plain text it is.
 */
export function MessageContent({ text }: { text: string }) {
  return <>{blocks(text)}</>;
}

function blocks(text: string): React.ReactNode[] {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const out: React.ReactNode[] = [];

  let paragraph: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;

  const flushParagraph = () => {
    if (!paragraph.length) return;
    out.push(
      <p key={`p-${out.length}`} className="whitespace-pre-wrap">
        {inline(paragraph.join(" "))}
      </p>,
    );
    paragraph = [];
  };

  const flushList = () => {
    if (!list) return;
    const items = list.items.map((item, index) => (
      <li key={index} className="pl-0.5">
        {inline(item)}
      </li>
    ));
    out.push(
      list.ordered ? (
        <ol key={`l-${out.length}`} className="ml-4 list-decimal space-y-1 marker:text-subtle">
          {items}
        </ol>
      ) : (
        <ul key={`l-${out.length}`} className="ml-4 list-disc space-y-1 marker:text-subtle">
          {items}
        </ul>
      ),
    );
    list = null;
  };

  for (const raw of lines) {
    const line = raw.trimEnd();

    if (!line.trim()) {
      flushParagraph();
      flushList();
      continue;
    }

    const heading = /^#{1,4}\s+(.*)$/.exec(line);
    if (heading) {
      flushParagraph();
      flushList();
      out.push(
        <p key={`h-${out.length}`} className="font-semibold text-content">
          {inline(heading[1] ?? "")}
        </p>,
      );
      continue;
    }

    const bullet = /^\s*[-*•]\s+(.*)$/.exec(line);
    if (bullet) {
      flushParagraph();
      if (!list || list.ordered) {
        flushList();
        list = { ordered: false, items: [] };
      }
      list.items.push(bullet[1] ?? "");
      continue;
    }

    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (numbered) {
      flushParagraph();
      if (!list || !list.ordered) {
        flushList();
        list = { ordered: true, items: [] };
      }
      list.items.push(numbered[1] ?? "");
      continue;
    }

    // A continuation line inside a list belongs to the last item.
    if (list && /^\s{2,}\S/.test(raw)) {
      list.items[list.items.length - 1] += ` ${line.trim()}`;
      continue;
    }

    flushList();
    paragraph.push(line.trim());
  }

  flushParagraph();
  flushList();
  return out;
}

/** `**bold**`, `*italic*` and `` `code` ``, in one pass. */
function inline(text: string): React.ReactNode {
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\n]+\*)/g;
  const parts = text.split(pattern).filter((part) => part !== "");

  return parts.map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return (
        <strong key={index} className="font-semibold text-content">
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      return (
        <code
          key={index}
          className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[0.85em] text-content"
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    if (part.startsWith("*") && part.endsWith("*") && part.length > 2) {
      return <em key={index}>{part.slice(1, -1)}</em>;
    }
    return <Fragment key={index}>{part}</Fragment>;
  });
}
