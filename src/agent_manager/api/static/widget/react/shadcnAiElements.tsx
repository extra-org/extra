import { CheckCircleIcon, CircleIcon, SendIcon, WrenchIcon, XCircleIcon } from "lucide-react";
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
    <textarea
      {...props}
      className={cn("input", props.className)}
      name="message"
      onInput={(event) => {
        props.onInput?.(event);
        const input = event.currentTarget;
        input.style.height = "auto";
        input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
      }}
      onKeyDown={onKeyDown}
      ref={inputRef}
      rows={props.rows ?? 1}
    />
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
