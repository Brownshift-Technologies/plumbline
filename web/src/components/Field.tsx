import type { CSSProperties, InputHTMLAttributes } from "react";
import { useId } from "react";

export interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  hint?: string;
  /**
   * When true, `hint` is validation feedback rather than a neutral tip: it
   * gets `role="alert"` so assistive tech announces it the moment it
   * appears, not only when the field happens to receive focus (which is
   * all a plain `aria-describedby` association guarantees). Also sets
   * `aria-invalid` on the input.
   */
  invalid?: boolean;
  /** Style for the wrapping .field div, distinct from the <input>'s own style. */
  wrapperStyle?: CSSProperties;
}

export function Field({ label, hint, invalid, id, wrapperStyle, ...rest }: FieldProps) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  const hintId = hint ? `${inputId}-hint` : undefined;

  return (
    <div className="field" style={wrapperStyle}>
      <label htmlFor={inputId}>{label}</label>
      <input id={inputId} aria-describedby={hintId} aria-invalid={invalid || undefined} {...rest} />
      {hint && (
        <span id={hintId} className="hint" role={invalid ? "alert" : undefined}>
          {hint}
        </span>
      )}
    </div>
  );
}
