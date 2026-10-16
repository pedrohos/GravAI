import type { ComponentPropsWithoutRef, ReactNode } from "react";

type PanelProps = Omit<ComponentPropsWithoutRef<"section">, "title"> & {
  title?: ReactNode;
};

export function Panel({ title, children, className = "", ...props }: PanelProps) {
  return (
    <section {...props} className={`panel ${className}`.trim()}>
      {title && <h2>{title}</h2>}
      {children}
    </section>
  );
}

type SectionHeaderProps = Omit<ComponentPropsWithoutRef<"div">, "title"> & {
  title: ReactNode;
  titleId?: string;
};

export function SectionHeader({ title, titleId, children, className = "", ...props }: SectionHeaderProps) {
  return (
    <div {...props} className={`section-head ${className}`.trim()}>
      <h2 id={titleId}>{title}</h2>
      {children}
    </div>
  );
}

export function PageHeader({ title, subtitle, className = "" }: {
  title: ReactNode;
  subtitle?: ReactNode;
  className?: string;
}) {
  return (
    <>
      <h1 className={className || undefined}>{title}</h1>
      {subtitle && <p className="subtitle">{subtitle}</p>}
    </>
  );
}
