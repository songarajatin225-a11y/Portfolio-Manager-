import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva("inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider", {
  variants: {
    variant: {
      default: "bg-surface-3 text-muted",
      up: "bg-up/15 text-up",
      down: "bg-down/15 text-down",
      warn: "bg-warn/15 text-warn",
      info: "bg-info/15 text-info",
      accent: "bg-accent/20 text-accent",
      live: "bg-live/15 text-live",
      delayed: "bg-delayed/15 text-delayed",
      historical: "bg-historical/15 text-historical",
      simulated: "bg-simulated/20 text-simulated",
    },
  },
  defaultVariants: { variant: "default" },
});

export function Badge({ className, variant, ...props }: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
