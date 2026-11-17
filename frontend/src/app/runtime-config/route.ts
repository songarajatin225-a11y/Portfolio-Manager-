import { connection } from "next/server";

/** Public runtime configuration (no secrets). Read at request time so one build serves every environment. */
export async function GET(): Promise<Response> {
  await connection();
  const wsUrl = process.env.PUBLIC_WS_URL ?? "ws://localhost:8000/ws/stream";
  return Response.json({ wsUrl }, { headers: { "Cache-Control": "no-store" } });
}
