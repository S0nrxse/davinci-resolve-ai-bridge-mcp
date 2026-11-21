import { createElement, Fragment, type ReactNode } from "react";
import { isMediaName, linkify, SeekLink, TIMECODE } from "./seek";

/**
 * A deliberately small Markdown renderer: paragraphs, lists, code, bold, italics, inline code, links, GitHub tables,
 * and a short allowlist of inline HTML (below). Everything is built as React elements, never as HTML strings, so
 * anything outside the allowlist shows as the literal text it is.
 */

// ------------------------------------------------------------- inline Markdown

function markdownInline(text: string, key = 0): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\s][^*]*\*)|(\[[^\]]+\]\((https?:\/\/[^)\s]+)\))/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = key;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(<Fragment key={i++}>{linkify(text.slice(last, m.index))}</Fragment>);
    const tok = m[0];
    const code = tok.slice(1, -1);
    // Clip names in code spans and timecodes move Resolve's playhead when clicked.
    if (m[1] && isMediaName(code))
      out.push(
        <SeekLink key={i++} clip={code} code>
          <code>{code}</code>
        </SeekLink>,
      );
    else if (m[1] && new RegExp(`^${TIMECODE.source}$`).test(code))
      out.push(
        <SeekLink key={i++} timecode={code} code>
          <code>{code}</code>
        </SeekLink>,
      );
    else if (m[1]) out.push(<code key={i++}>{code}</code>);
    else if (m[2]) out.push(<strong key={i++}>{linkify(tok.slice(2, -2))}</strong>);
    else if (m[3]) out.push(<em key={i++}>{linkify(tok.slice(1, -1))}</em>);
    else if (m[4]) {
      const label = tok.slice(1, tok.indexOf("]("));
      out.push(
        <a key={i++} href={m[5]} target="_blank" rel="noreferrer">
          {label}
        </a>,
      );
    }
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(<Fragment key={i++}>{linkify(text.slice(last))}</Fragment>);
  return out;
}

// ------------------------------------------------------------- inline HTML

/** The only tags the model's HTML may use; no attributes except `open` on details. */
export const HTML_ALLOWED = new Set(["kbd", "sub", "sup", "mark", "br", "details", "summary", "b", "i", "em", "strong", "code", "u", "s", "small"]);
const TAG = /<(\/?)([a-zA-Z][a-zA-Z0-9]*)((?:\s+[a-zA-Z-]+(?:=(?:"[^"]*"|'[^']*'|[^\s>]+))?)*)\s*(\/?)>/g;

type Tok = { t: "text"; s: string } | { t: "open"; tag: string; open?: boolean } | { t: "close"; tag: string } | { t: "br" };
type Node = { tag: string; open?: boolean; kids: (Node | string)[] };

/** Split text into Markdown text and allowed tags; code spans are left whole (tags inside them stay literal). */
function tokens(text: string): Tok[] {
  const out: Tok[] = [];
  const parts = text.split(/(`[^`]+`)/);
  for (const part of parts) {
    if (part.startsWith("`") && part.endsWith("`") && part.length > 1) {
      out.push({ t: "text", s: part });
      continue;
    }
    let last = 0;
    let m: RegExpExecArray | null;
    TAG.lastIndex = 0;
    while ((m = TAG.exec(part))) {
      const tag = m[2].toLowerCase();
      if (!HTML_ALLOWED.has(tag)) continue; // stays as literal text
      if (m.index > last) out.push({ t: "text", s: part.slice(last, m.index) });
      if (tag === "br") out.push({ t: "br" });
      else if (m[1]) out.push({ t: "close", tag });
      else if (!m[4]) out.push({ t: "open", tag, open: tag === "details" && /(^|\s)open(\s|=|$)/i.test(m[3]) });
      last = m.index + m[0].length;
    }
    if (last < part.length) out.push({ t: "text", s: part.slice(last) });
  }
  return out;
}

function tree(toks: Tok[]): Node {
  const root: Node = { tag: "", kids: [] };
  const stack: Node[] = [root];
  const top = () => stack[stack.length - 1];
  for (const k of toks) {
    if (k.t === "text") top().kids.push(k.s);
    else if (k.t === "br") top().kids.push({ tag: "br", kids: [] });
    else if (k.t === "open") {
      const node: Node = { tag: k.tag, open: k.open, kids: [] };
      top().kids.push(node);
      stack.push(node);
    } else {
      // A close tag closes the nearest matching open one (and anything left open inside it); a stray one is dropped.
      const at = stack.map((n) => n.tag).lastIndexOf(k.tag);
      if (at > 0) stack.length = at;
    }
  }
  return root;
}

let keySeq = 0;
function render(node: Node | string): ReactNode {
  if (typeof node === "string") return <Fragment key={keySeq++}>{markdownInline(node, keySeq++ * 1000)}</Fragment>;
  if (node.tag === "br") return <br key={keySeq++} />;
  const kids = node.kids.map(render);
  if (node.tag === "details") return createElement("details", { key: keySeq++, open: node.open || undefined }, kids);
  return createElement(node.tag, { key: keySeq++ }, kids);
}

/** Markdown inline content with the HTML allowlist applied. */
export function inline(text: string, _key = 0): ReactNode[] {
  if (!text.includes("<")) return markdownInline(text, _key);
  return tree(tokens(text)).kids.map(render);
}

// ------------------------------------------------------------- tables

const SEP = /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/;
function cells(line: string): string[] {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  const out: string[] = [];
  let cur = "";
  let inCode = false;
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (ch === "\\" && s[i + 1] === "|") {
      cur += "|";
      i++;
    } else if (ch === "`") {
      inCode = !inCode;
      cur += ch;
    } else if (ch === "|" && !inCode) {
      out.push(cur.trim());
      cur = "";
    } else cur += ch;
  }
  out.push(cur.trim());
  return out;
}
const isTableStart = (lines: string[], i: number) => lines[i].includes("|") && i + 1 < lines.length && SEP.test(lines[i + 1]) && lines[i + 1].includes("-");

function Table({ head, align, rows }: { head: string[]; align: ("left" | "center" | "right" | undefined)[]; rows: string[][] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {head.map((h, j) => (
              <th key={j} style={{ textAlign: align[j] }}>
                {inline(h)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {head.map((_, j) => (
                <td key={j} style={{ textAlign: align[j] }}>
                  {inline(r[j] ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ------------------------------------------------------------- blocks

export function Markdown({ text }: { text: string }) {
  const lines = text.replace(/\r/g, "").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let k = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith("```")) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) body.push(lines[i++]);
      i++;
      blocks.push(
        <pre key={k++}>
          <code>{body.join("\n")}</code>
        </pre>,
      );
      continue;
    }
    if (isTableStart(lines, i)) {
      const head = cells(line);
      const align = cells(lines[i + 1]).map((c) => (c.startsWith(":") && c.endsWith(":") ? "center" : c.endsWith(":") ? "right" : c.startsWith(":") ? "left" : undefined)) as (
        | "left"
        | "center"
        | "right"
        | undefined
      )[];
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim() && lines[i].includes("|")) rows.push(cells(lines[i++]));
      blocks.push(<Table key={k++} head={head} align={align} rows={rows} />);
      continue;
    }
    if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const items: string[] = [];
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*([-*]|\d+\.)\s+/, ""));
        i++;
      }
      const List = ordered ? "ol" : "ul";
      blocks.push(
        <List key={k++}>
          {items.map((it, j) => (
            <li key={j}>{inline(it)}</li>
          ))}
        </List>,
      );
      continue;
    }
    if (/^#{1,4}\s+/.test(line)) {
      blocks.push(
        <p key={k++}>
          <strong>{inline(line.replace(/^#{1,4}\s+/, ""))}</strong>
        </p>,
      );
      i++;
      continue;
    }
    if (!line.trim()) {
      i++;
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^(```|\s*([-*]|\d+\.)\s+|#{1,4}\s)/.test(lines[i]) && !isTableStart(lines, i)) para.push(lines[i++]);
    // A paragraph is one inline run (lines joined by <br>), so an HTML element may span its lines.
    const run = para.join("<br>");
    // A details element can't live inside <p>; such a run gets a plain block instead.
    blocks.push(/<details/i.test(run) ? <div key={k++} className="md-block">{inline(run)}</div> : <p key={k++}>{inline(run)}</p>);
  }
  return <div className="prose-r">{blocks}</div>;
}
