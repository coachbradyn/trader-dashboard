import { cn } from "@/lib/utils";

function Skeleton({
  className,
  variant = "default",
  ...props
}: React.HTMLAttributes<HTMLDivElement> & {
  variant?: "default" | "ai";
}) {
  return (
    <div
      className={cn(
        "animate-pulse rounded-lg",
        variant === "ai" ? "ai-skeleton" : "bg-surface-light",
        className
      )}
      {...props}
    />
  );
}

export { Skeleton };
