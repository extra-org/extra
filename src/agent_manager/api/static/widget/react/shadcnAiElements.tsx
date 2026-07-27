import {
  CheckCircleIcon,
  CircleIcon,
  SendIcon,
  ThumbsDownIcon,
  ThumbsUpIcon,
  WrenchIcon,
  XCircleIcon,
} from "lucide-react";
import type {
  ComponentProps,
  FormEvent,
  KeyboardEvent,
  PropsWithChildren,
  RefObject,
} from "react";
import { memo } from "react";
import { Streamdown } from "streamdown";
import { StickToBottom } from "use-stick-to-bottom";

type ClassValue = string | false | null | undefined;

function cn(...values: ClassValue[]): string {
  return values.filter(Boolean).join(" ");
}

export type ConversationProps = ComponentProps<typeof StickToBottom>;

export function Conversation({ children, className, ...props }: ConversationProps) {
  return (
    <StickToBottom
      aria-live="polite"
      aria-relevant="additions text"
      className={cn("messages", className)}
      initial="smooth"
      resize="smooth"
      role="log"
      {...props}
    >
      {children}
    </StickToBottom>
  );
}

export type ConversationContentProps = ComponentProps<typeof StickToBottom.Content>;

export function ConversationContent({ className, ...props }: ConversationContentProps) {
  return <StickToBottom.Content className={cn("conversation-content", className)} {...props} />;
}

export function Message({
  children,
  from,
  typing = false,
}: PropsWithChildren<{ from: "user" | "assistant"; typing?: boolean }>) {
  return (
    <div
      className={cn("msg", from === "user" ? "user" : "ai", typing && "typing")}
      role={typing ? "status" : undefined}
      aria-label={typing ? "Assistant is typing" : undefined}
    >
      {children}
    </div>
  );
}

export function MessageContent({ children }: PropsWithChildren) {
  return <div className="message-content">{children}</div>;
}

export type MessageResponseProps = ComponentProps<typeof Streamdown> & {
  isAnimating?: boolean;
};

export const MessageResponse = memo(function MessageResponse({
  className,
  ...props
}: MessageResponseProps) {
  return <Streamdown className={cn("message-response", className)} {...props} />;
});

export function MessageFeedback({
  feedback,
  onFeedback,
}: {
  feedback?: "thumbs_up" | "thumbs_down" | null;
  onFeedback: (nextFeedback: "thumbs_up" | "thumbs_down" | null) => void;
}) {
  return (
    <div className="feedback-actions" role="group" aria-label="Was this response helpful?">
      <button
        aria-label="Thumbs up"
        aria-pressed={feedback === "thumbs_up"}
        className={cn("feedback-btn", feedback === "thumbs_up" && "active")}
        onClick={() => onFeedback(feedback === "thumbs_up" ? null : "thumbs_up")}
        type="button"
      >
        <ThumbsUpIcon aria-hidden="true" />
      </button>
      <button
        aria-label="Thumbs down"
        aria-pressed={feedback === "thumbs_down"}
        className={cn("feedback-btn", feedback === "thumbs_down" && "active")}
        onClick={() => onFeedback(feedback === "thumbs_down" ? null : "thumbs_down")}
        type="button"
      >
        <ThumbsDownIcon aria-hidden="true" />
      </button>
    </div>
  );
}

export interface PromptInputMessage {
  text: string;
}

export function PromptInput({
  children,
  className,
  onSubmit,
}: PropsWithChildren<{
  className?: string;
  onSubmit: (message: PromptInputMessage) => void;
}>) {
  function submit(form: HTMLFormElement) {
    const input = form.elements.namedItem("message") as HTMLTextAreaElement | null;
    const text = input?.value.trim() ?? "";
    if (!text) return;
    input!.value = "";
    input!.style.height = "auto";
    input!.style.overflowY = "hidden";
    onSubmit({ text });
  }

  function onFormSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    submit(event.currentTarget);
  }

  return (
    <form className={cn("composer", className)} onSubmit={onFormSubmit}>
      {children}
    </form>
  );
}

export function PromptInputTextarea({
  inputRef,
  onSubmit,
  ...props
}: ComponentProps<"textarea"> & {
  inputRef: RefObject<HTMLTextAreaElement | null>;
  onSubmit: () => void;
}) {
  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    props.onKeyDown?.(event);
    if (event.defaultPrevented) return;
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      onSubmit();
    }
  }

  return (
    <div className="input-wrap">
      <textarea
        {...props}
        className={cn("input", props.className)}
        name="message"
        onInput={(event) => {
          props.onInput?.(event);
          const input = event.currentTarget;
          input.style.height = "auto";
          input.style.height = `${Math.min(input.scrollHeight, 140)}px`;
          // Only scroll once we've hit the max height; otherwise it grows to fit.
          input.style.overflowY = input.scrollHeight > 140 ? "auto" : "hidden";
        }}
        onKeyDown={onKeyDown}
        ref={inputRef}
        rows={props.rows ?? 1}
      />
    </div>
  );
}

export function PromptInputFooter({ children }: PropsWithChildren) {
  return <div className="prompt-footer">{children}</div>;
}

export function PromptInputSubmit({ disabled }: { disabled: boolean }) {
  return (
    <button aria-label="Send message" className="send" disabled={disabled} type="submit">
      <SendIcon aria-hidden="true" />
    </button>
  );
}

export type ToolState = "input-available" | "output-available" | "output-error";

export function Tool({ children, defaultOpen = false }: PropsWithChildren<{ defaultOpen?: boolean }>) {
  return (
    <details className="tool" open={defaultOpen}>
      {children}
    </details>
  );
}

export function ToolHeader({ state, title }: { state: ToolState; title: string }) {
  return (
    <summary className="tool-header">
      <span className="tool-title">
        <WrenchIcon aria-hidden="true" />
        {title}
      </span>
      <span className={cn("tool-badge", state)}>
        {state === "output-available" ? <CheckCircleIcon aria-hidden="true" /> : null}
        {state === "output-error" ? <XCircleIcon aria-hidden="true" /> : null}
        {state === "input-available" ? <CircleIcon aria-hidden="true" /> : null}
        {statusLabel(state)}
      </span>
    </summary>
  );
}

export function ToolContent({ children }: PropsWithChildren) {
  return <div className="tool-content">{children}</div>;
}

export function ToolOutput({ errorText }: { errorText?: string | null }) {
  if (!errorText) return null;
  return <div className="tool-error">{errorText}</div>;
}

function statusLabel(state: ToolState): string {
  if (state === "output-available") return "Completed";
  if (state === "output-error") return "Error";
  return "Running";
}
