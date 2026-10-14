export function Icon({ name }: { name: "arrow-up" | "arrow-down" | "sort" | "arrow-left" | "arrow-right" | "external" | "record-dot" | "video" | "file-text" | "x" | "chevron-down" | "check" | "copy" | "info" }) {
  return <span aria-hidden="true" className={`icon icon-${name}`} style={{ maskImage: `url(/icons/${name}.svg)`, WebkitMaskImage: `url(/icons/${name}.svg)` }} />;
}
