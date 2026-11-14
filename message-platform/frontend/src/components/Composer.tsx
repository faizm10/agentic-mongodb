"use client";

import { AnimatePresence, motion } from "motion/react";
import { ArrowUp, Check, CornerUpLeft, Pencil, Smile, X } from "lucide-react";
import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import { firstName } from "@/lib/format";
import type { Message, User } from "@/lib/types";
import { useDismiss } from "./primitives";

const EMOJI: [string, string[]][] = [
  ["Smileys", ["😀", "😄", "😂", "🥹", "😊", "😍", "🥰", "😎", "🤩", "🤔", "😴", "😅", "😮", "😬", "🙃", "😇"]],
  ["Gestures", ["👍", "👎", "👏", "🙌", "🙏", "👋", "🤝", "💪", "✌️", "🤞", "👀", "🫶"]],
  ["Things", ["❤️", "🔥", "✨", "🎉", "🚀", "💯", "✅", "⚡", "☕", "🍕", "🎨", "📌", "💡", "📈", "🏔️", "🥾"]],
];

export const MAX_LENGTH = 4000;

export type ComposerHandle = { focus: () => void };

type Props = {
  conversationId: string;
  me: string;
  users: Record<string, User>;
  replyTo: Message | null;
  editing: Message | null;
  disabled?: boolean;
  onCancelReply: () => void;
  onCancelEdit: () => void;
  onSend: (body: string) => void;
  onSaveEdit: (id: string, body: string) => void;
  onEditLast: () => void;
  onTyping: (isTyping: boolean) => void;
};

export function EmojiGrid({ onPick }: { onPick: (emoji: string) => void }) {
  return (
    <div className="w-[292px] p-2">
      {EMOJI.map(([group, items]) => (
        <div key={group} className="mb-1.5">
          <p className="px-1.5 pb-1 pt-1 text-[10.5px] font-semibold uppercase tracking-wider text-faint">{group}</p>
          <div className="grid grid-cols-8">
            {items.map((e) => (
              <button key={e} type="button" onClick={() => onPick(e)} className="flex h-8 w-8 items-center justify-center rounded-lg text-xl transition hover:scale-110 hover:bg-sunken" aria-label={`Insert ${e}`}>
                {e}
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

const drafts = new Map<string, string>();

export const Composer = forwardRef<ComposerHandle, Props>(function Composer(p, ref) {
  const [text, setText] = useState(() => drafts.get(p.conversationId) ?? "");
  const [emojiOpen, setEmojiOpen] = useState(false);
  const area = useRef<HTMLTextAreaElement>(null);
  const typingTimer = useRef<number | undefined>(undefined);
  const lastTypingSent = useRef(0);
  const emojiRef = useDismiss(emojiOpen, () => setEmojiOpen(false));

  useImperativeHandle(ref, () => ({ focus: () => area.current?.focus() }));

  // Restore the per-conversation draft when switching chats.
  useEffect(() => {
    setText(drafts.get(p.conversationId) ?? "");
    window.setTimeout(() => area.current?.focus(), 0);
  }, [p.conversationId]);

  useEffect(() => {
    if (p.editing) {
      setText(p.editing.body);
      window.setTimeout(() => {
        const el = area.current;
        if (el) {
          el.focus();
          el.setSelectionRange(el.value.length, el.value.length);
        }
      }, 0);
    }
  }, [p.editing]);

  useEffect(() => {
    if (p.replyTo) area.current?.focus();
  }, [p.replyTo]);

  // Auto-grow.
  useEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, 168)}px`;
  }, [text]);

  const stopTyping = useCallback(() => {
    window.clearTimeout(typingTimer.current);
    if (lastTypingSent.current) {
      lastTypingSent.current = 0;
      p.onTyping(false);
    }
  }, [p]);

  useEffect(() => () => window.clearTimeout(typingTimer.current), []);

  const update = (value: string) => {
    setText(value);
    if (!p.editing) drafts.set(p.conversationId, value);
    if (!value.trim()) {
      stopTyping();
      return;
    }
    const now = Date.now();
    if (now - lastTypingSent.current > 2500) {
      lastTypingSent.current = now;
      p.onTyping(true);
    }
    window.clearTimeout(typingTimer.current);
    typingTimer.current = window.setTimeout(stopTyping, 3000);
  };

  const submit = () => {
    const body = text.trim();
    if (!body || body.length > MAX_LENGTH || p.disabled) return;
    if (p.editing) {
      p.onSaveEdit(p.editing.id, body);
    } else {
      p.onSend(body);
      drafts.delete(p.conversationId);
    }
    setText("");
    stopTyping();
    area.current?.focus();
  };

  const insert = (emoji: string) => {
    const el = area.current;
    const start = el?.selectionStart ?? text.length;
    const end = el?.selectionEnd ?? text.length;
    const next = text.slice(0, start) + emoji + text.slice(end);
    update(next);
    window.setTimeout(() => {
      el?.focus();
      el?.setSelectionRange(start + emoji.length, start + emoji.length);
    }, 0);
  };

  const over = text.length > MAX_LENGTH;
  const canSend = text.trim().length > 0 && !over && !p.disabled;
  const context = p.editing ?? p.replyTo;

  return (
    <div className="mx-auto w-full max-w-3xl px-3 pb-3 pt-1 sm:px-6 sm:pb-5">
      <div className="glass rounded-[22px] border border-line shadow-float transition focus-within:border-accent/40 focus-within:ring-4 focus-within:ring-accent/10">
        <AnimatePresence initial={false}>
          {context && (
            <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
              <div className="flex items-center gap-3 border-b border-line px-4 py-2.5">
                {p.editing ? <Pencil className="h-4 w-4 flex-none text-accent" /> : <CornerUpLeft className="h-4 w-4 flex-none text-accent" />}
                <div className="min-w-0 flex-1 border-l-2 border-accent/60 pl-2.5">
                  <p className="text-xs font-semibold text-accent">
                    {p.editing ? "Editing message" : `Replying to ${context.sender_id === p.me ? "yourself" : firstName(p.users[context.sender_id]?.name ?? "")}`}
                  </p>
                  <p className="truncate text-xs text-muted">{context.body}</p>
                </div>
                <button className="icon-btn h-7 w-7" onClick={() => { if (p.editing) { p.onCancelEdit(); setText(drafts.get(p.conversationId) ?? ""); } else p.onCancelReply(); }} aria-label="Cancel">
                  <X className="h-4 w-4" />
                </button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
        <div className="flex items-end gap-1.5 p-1.5 pl-2">
          <div className="relative" ref={emojiRef}>
            <button type="button" className="icon-btn h-10 w-10" onClick={() => setEmojiOpen((o) => !o)} aria-label="Emoji" aria-expanded={emojiOpen}>
              <Smile className="h-5 w-5" />
            </button>
            <AnimatePresence>
              {emojiOpen && (
                <motion.div initial={{ opacity: 0, y: 8, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 8, scale: 0.97 }} className="absolute bottom-12 left-0 z-30 rounded-2xl bg-raised shadow-pop">
                  <EmojiGrid onPick={insert} />
                </motion.div>
              )}
            </AnimatePresence>
          </div>
          <textarea
            ref={area}
            rows={1}
            value={text}
            disabled={p.disabled}
            onChange={(e) => update(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                submit();
              } else if (e.key === "Escape") {
                if (emojiOpen) setEmojiOpen(false);
                else if (p.editing) { p.onCancelEdit(); setText(drafts.get(p.conversationId) ?? ""); }
                else if (p.replyTo) p.onCancelReply();
              } else if (e.key === "ArrowUp" && !text && !p.editing) {
                e.preventDefault();
                p.onEditLast();
              }
            }}
            placeholder={p.disabled ? "Reconnecting…" : "Message"}
            aria-label="Message"
            className="scroll-thin max-h-[168px] min-h-[40px] flex-1 resize-none bg-transparent px-1 py-2.5 text-[15px] leading-5 outline-none placeholder:text-faint"
          />
          {text.length > MAX_LENGTH - 300 && (
            <span className={`self-center text-[11px] tabular-nums ${over ? "font-semibold text-danger" : "text-faint"}`}>{MAX_LENGTH - text.length}</span>
          )}
          <motion.button
            type="button"
            onClick={submit}
            disabled={!canSend}
            whileTap={{ scale: 0.9 }}
            className={`flex h-10 w-10 flex-none items-center justify-center rounded-full transition ${canSend ? "bg-accent text-white shadow-md shadow-accent/30" : "bg-sunken text-faint"}`}
            aria-label={p.editing ? "Save edit" : "Send message"}
          >
            {p.editing ? <Check className="h-5 w-5" /> : <ArrowUp className="h-5 w-5" strokeWidth={2.5} />}
          </motion.button>
        </div>
      </div>
      <p className="mt-1.5 hidden px-2 text-[11px] text-faint sm:block">
        <kbd className="font-sans font-semibold">Enter</kbd> to send · <kbd className="font-sans font-semibold">Shift + Enter</kbd> for a new line · <kbd className="font-sans font-semibold">↑</kbd> to edit your last message
      </p>
    </div>
  );
});
