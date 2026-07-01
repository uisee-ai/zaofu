// Shared composer submit/interrupt button. One button that morphs by
// ComposerStatus so the channel composer (ChannelPage) and the headless
// composer (OrchestratorPanel) behave identically:
//   idle/error → send arrow (type=submit)
//   submitted  → spinner (in flight, no token yet)
//   streaming  → filled square → onStop (Interrupt), type=button
// Styling is left to callers via
// `className` so each surface keeps its existing look.

import type { ButtonHTMLAttributes, ReactNode } from "react";
import { ArrowUp, Loader2, Square } from "lucide-react";
import type { ComposerStatus } from "./workState";

interface ComposerSubmitButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "type"> {
  status: ComposerStatus;
  /** When set and status is streaming, the button interrupts instead of submitting. */
  onStop?: () => void;
  iconSize?: number;
  /** Extra trailing content (e.g. a split-button chevron) kept after the icon. */
  children?: ReactNode;
}

export function ComposerSubmitButton({
  status,
  onStop,
  iconSize = 16,
  disabled,
  className,
  title,
  children,
  onClick,
  ...rest
}: ComposerSubmitButtonProps) {
  const canStop = status === "streaming" && Boolean(onStop);
  let icon = <ArrowUp size={iconSize} />;
  if (status === "submitted") icon = <Loader2 className="composer-submit-spin" size={iconSize} />;
  else if (status === "streaming") icon = <Square className="composer-submit-stop" size={iconSize} />;
  return (
    <button
      {...rest}
      aria-label={canStop ? "Interrupt" : "Send message"}
      className={className}
      // While generating without a stop handler the button is inert; with a
      // stop handler it stays clickable so the user can interrupt.
      disabled={canStop ? false : disabled || status === "submitted" || status === "streaming"}
      onClick={(event) => {
        if (canStop) {
          event.preventDefault();
          onStop?.();
          return;
        }
        onClick?.(event);
      }}
      title={canStop ? "Interrupt" : title}
      type={canStop ? "button" : "submit"}
    >
      {icon}
      {children}
    </button>
  );
}
