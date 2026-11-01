import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PLAN_PAGES, PLAN_SLUGS } from "../planos-config";
import CheckoutForm from "./CheckoutForm";

// Página individual de plano — landing focada numa única oferta, com checkout
// embutido. O conteúdo vem do config central (../planos-config.ts).

export function generateStaticParams() {
  return PLAN_SLUGS.map(slug => ({ slug }));
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const { slug } = await params;
  const p = PLAN_PAGES[slug];
  if (!p) return {};
  const title = `${p.nome} — ${p.price} ${p.per} | KLEAD`;
  return {
    title,
    description: p.metaDescription,
    openGraph: { title, description: p.metaDescription },
  };
}

export default async function PlanoPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const p = PLAN_PAGES[slug];
  if (!p) notFound();

  return (
    <div className="min-h-screen bg-[#F0EDE8] flex flex-col">
      {/* Header */}
      <header className="border-b border-[#D4CFC9] px-4 py-4">
        <div className="max-w-3xl mx-auto flex items-center justify-between">
          <Link href="/plans" className="text-2xl font-bold text-[#CC3300]">KLEAD</Link>
          <Link href="/plans" className="text-[#6B5E55] hover:text-[#CC3300] text-xs font-semibold transition-colors">
            Ver todos os planos →
          </Link>
        </div>
      </header>

      <main className="flex-1 px-4 py-12">
        <div className="max-w-3xl mx-auto">
          {/* Hero da oferta */}
          <div className="text-center mb-10">
            {p.badge && (
              <span className="inline-block bg-[#CC3300] text-white text-xs font-bold uppercase tracking-wide px-3 py-1.5 rounded-full mb-4">
                {p.badge}
              </span>
            )}
            <h1 className="text-[#1A1410] text-3xl sm:text-4xl font-bold leading-tight">{p.headline}</h1>
            <p className="text-[#6B5E55] text-base mt-4 max-w-xl mx-auto">{p.sub}</p>
          </div>

          {/* Card da oferta: preço + features + checkout */}
          <div className="grid gap-6 md:grid-cols-2 items-start">
            <div className="rounded-2xl border border-[#D4CFC9] bg-[#E8E4DF] p-6">
              <p className="text-[#1A1410] font-bold text-lg">{p.nome}</p>
              <p className="text-[#1A1410] font-bold text-4xl mt-2">{p.price}</p>
              <p className="text-[#6B5E55] text-sm">{p.per}</p>
              <p className="text-[#6B5E55] text-xs mt-2 mb-5">{p.note}</p>
              <ul className="space-y-2.5">
                {p.features.map(f => (
                  <li key={f} className="flex items-start gap-2 text-[#1A1410] text-xs">
                    <span className="text-[#CC3300] font-bold mt-0.5">✓</span>
                    <span>{f}</span>
                  </li>
                ))}
              </ul>
            </div>

            <div className="rounded-2xl border border-[#CC3300] ring-2 ring-[#CC3300]/20 bg-[#E8E4DF] p-6">
              <p className="text-[#1A1410] font-bold text-sm mb-4">Garanta agora — acesso liberado na hora</p>
              <CheckoutForm plano={p.plano} ciclo={p.ciclo} cta={p.cta} />
            </div>
          </div>

          {/* FAQ da oferta */}
          <div className="mt-12">
            <h2 className="text-[#1A1410] text-xl font-bold text-center mb-6">Perguntas frequentes</h2>
            <div className="space-y-3">
              {p.faq.map(item => (
                <details key={item.q} className="rounded-2xl border border-[#D4CFC9] bg-[#E8E4DF] px-5 py-4 group">
                  <summary className="text-[#1A1410] font-semibold text-sm cursor-pointer list-none flex items-center justify-between gap-4">
                    {item.q}
                    <span className="text-[#CC3300] font-bold group-open:rotate-45 transition-transform">+</span>
                  </summary>
                  <p className="text-[#6B5E55] text-xs leading-relaxed mt-3">{item.a}</p>
                </details>
              ))}
            </div>
          </div>

          <p className="text-center mt-10">
            <Link href="/plans" className="text-[#CC3300] text-sm font-semibold underline">
              Comparar todos os planos
            </Link>
          </p>
        </div>
      </main>

      {/* Footer */}
      <footer className="border-t border-[#D4CFC9] px-4 py-6 text-center">
        <p className="text-[#6B5E55] text-xs">
          © {new Date().getFullYear()} KLEAD. Qualificação automática de leads via WhatsApp com IA.
        </p>
      </footer>
    </div>
  );
}
