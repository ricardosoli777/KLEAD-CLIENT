// Somente configuração pública; nenhuma chave ou senha é retornada.
export const dynamic = "force-dynamic";

export function GET() {
  return Response.json(
    { evolution_api_url: process.env.EVO_API_URL || "" },
    { headers: { "Cache-Control": "no-store" } },
  );
}
