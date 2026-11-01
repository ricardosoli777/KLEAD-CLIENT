import { NextRequest, NextResponse } from "next/server";

export const maxDuration = 120;

const BACKEND_URL = process.env.BACKEND_URL || "http://backend:8000";
const API_KEY = process.env.API_KEY || "";

type Context = { params: Promise<{ path: string[] }> };

async function handler(req: NextRequest, ctx: Context) {
  const { path } = await ctx.params;
  const joined = path.join("/");
  const search = req.nextUrl.search;
  const targetUrl = `${BACKEND_URL}/${joined}${search}`;

  const headers: Record<string, string> = {
    "X-API-Key": API_KEY,
  };

  // Passa token de autenticação do usuário para o backend
  const authToken = req.cookies.get("klead_token")?.value || req.headers.get("x-auth-token") || "";
  if (authToken) headers["X-Auth-Token"] = authToken;

  const contentType = req.headers.get("content-type");
  if (contentType) headers["Content-Type"] = contentType;
  const range = req.headers.get("range");
  if (range) headers["Range"] = range;

  // Encaminha o corpo como stream em vez de bufferizar tudo com arrayBuffer() —
  // uploads de vídeo maiores (10MB+) travavam com "campo obrigatório ausente"
  // porque o corpo inteiro precisava caber em memória antes de seguir adiante.
  const hasBody = !["GET", "HEAD"].includes(req.method) && req.body;

  const upstream = await fetch(targetUrl, {
    method: req.method,
    headers,
    body: hasBody ? req.body : undefined,
    // @ts-expect-error -- "duplex" é exigido pelo undici (fetch do Node) ao
    // enviar um corpo em streaming, mas ainda não está no tipo do RequestInit.
    duplex: hasBody ? "half" : undefined,
  });

  const responseHeaders: Record<string, string> = {};
  const ct = upstream.headers.get("content-type");
  if (ct) responseHeaders["Content-Type"] = ct;
  const cd = upstream.headers.get("content-disposition");
  if (cd) responseHeaders["Content-Disposition"] = cd;
  const cl = upstream.headers.get("content-length");
  if (cl) responseHeaders["Content-Length"] = cl;
  const cr = upstream.headers.get("content-range");
  if (cr) responseHeaders["Content-Range"] = cr;
  const ar = upstream.headers.get("accept-ranges");
  if (ar) responseHeaders["Accept-Ranges"] = ar;

  // SSE: repassa headers anti-buffering para o stream fluir por proxies/Traefik
  if (ct?.includes("text/event-stream")) {
    responseHeaders["Cache-Control"] = "no-cache";
    responseHeaders["X-Accel-Buffering"] = "no";
  }

  const response = new NextResponse(upstream.body, {
    status: upstream.status,
    headers: responseHeaders,
  });

  const setCookies =
    typeof (upstream.headers as Headers & { getSetCookie?: () => string[] }).getSetCookie === "function"
      ? (upstream.headers as Headers & { getSetCookie: () => string[] }).getSetCookie()
      : (upstream.headers.get("set-cookie") ? [upstream.headers.get("set-cookie") as string] : []);

  for (const cookie of setCookies) {
    response.headers.append("Set-Cookie", cookie);
  }

  return response;
}

export const GET = handler;
export const POST = handler;
export const PUT = handler;
export const PATCH = handler;
export const DELETE = handler;
