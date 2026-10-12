/** A status, coloured by what it is. The class names are the statuses
 *  themselves - see the .pill rules in globals.css. */
export function Pill({ status }: { status: string }) {
  return <span className={`pill ${status}`}>{status}</span>;
}
