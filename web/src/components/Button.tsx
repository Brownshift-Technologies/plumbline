import type { ButtonHTMLAttributes, ReactNode } from "react";

export type ButtonVariant = "default" | "pri" | "dang";
export type ButtonSize = "md" | "sm";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  children: ReactNode;
}

export function Button({
  variant = "default",
  size = "md",
  className,
  children,
  ...rest
}: ButtonProps) {
  const classes = [
    "btn",
    variant === "pri" ? "pri" : null,
    variant === "dang" ? "dang" : null,
    size === "sm" ? "sm" : null,
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <button className={classes} {...rest}>
      {children}
    </button>
  );
}
