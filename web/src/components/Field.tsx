import type { CSSProperties, InputHTMLAttributes } from "react";
import { useId } from "react";

export interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  hint?: string;
  /** Style for the wrapping .field div, distinct from the <input>'s own style. */
  wrapperStyle?: CSSProperties;
}

export function Field({ label, hint, id, wrapperStyle, ...rest }: FieldProps) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  const hintId = hint ? `${inputId}-hint` : undefined;

  return (
    <div className="field" style={wrapperStyle}>
      <label htmlFor={inputId}>{label}</label>
      <input id={inputId} aria-describedby={hintId} {...rest} />
      {hint && (
        <span id={hintId} className="hint">
          {hint}
        </span>
      )}
    </div>
  );
}
