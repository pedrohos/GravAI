import { redirect } from "next/navigation";

/** Jobs is where work is asked for, so it is where the page opens. */
export default function Home() {
  redirect("/jobs");
}
