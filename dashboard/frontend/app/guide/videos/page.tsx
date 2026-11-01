"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import PageHeaderShell from "@/components/PageHeaderShell";
import { listGuideVideos, createGuideVideo, updateGuideVideo, deleteGuideVideo, type GuideVideo } from "@/lib/api";

// ── Card de vídeo ─────────────────────────────────────────────────────────────

function VideoCard({ video, number, isSuperadmin, onPlay, onEdit, onDelete }: {
  video: GuideVideo;
  number: number;
  isSuperadmin: boolean;
  onPlay: (v: GuideVideo) => void;
  onEdit: (v: GuideVideo) => void;
  onDelete: (v: GuideVideo) => void;
}) {
  return (
    <div className="bg-[#E8E4DF] border border-[#D4CFC9] rounded-2xl overflow-hidden">
      <div className="aspect-video w-full bg-[#1A1410]">
        {video.kind === "mp4" ? (
          <video src={video.url} controls className="h-full w-full" preload="metadata" />
        ) : (
          <iframe
            src={video.url}
            title={video.title}
            className="h-full w-full"
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
            allowFullScreen
          />
        )}
      </div>
      <div className="p-4 flex items-start justify-between gap-2">
        <div>
          <p className="text-[#1A1410] text-sm font-semibold leading-snug">
            <span className="text-[#6B5E55] font-bold mr-1.5">{number}.</span>
            {video.title}
          </p>
          {video.description && <p className="text-[#6B5E55] text-xs mt-1">{video.description}</p>}
          <button
            onClick={() => onPlay(video)}
            className="mt-2 text-[11px] font-semibold px-3 py-1.5 rounded-lg bg-[#1A1410] text-[#F0EDE8] hover:opacity-90 transition-opacity"
          >
            ▶ Assistir
          </button>
        </div>
        {isSuperadmin && (
          <div className="flex gap-1 shrink-0">
            <button
              onClick={() => onEdit(video)}
              className="text-[10px] font-semibold px-2 py-1 rounded-lg border border-[#D4CFC9] text-[#6B5E55] hover:border-[#3D4F5A] hover:text-[#1A1410] transition-colors"
            >
              Editar
            </button>
            <button
              onClick={() => onDelete(video)}
              className="text-[10px] font-semibold px-2 py-1 rounded-lg border border-[#D4CFC9] text-[#CC3300] hover:border-[#CC3300] transition-colors"
            >
              Apagar
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Player em modal (tela média / cheia) ─────────────────────────────────────

function PlayerModal({ video, onClose }: { video: GuideVideo; onClose: () => void }) {
  const playerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [onClose]);

  function goFullscreen() {
    playerRef.current?.requestFullscreen?.().catch(() => {});
  }

  return (
    <div
      className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-4xl bg-[#E8E4DF] border border-[#D4CFC9] rounded-2xl overflow-hidden shadow-2xl"
        onClick={e => e.stopPropagation()}
      >
        <div className="px-4 py-3 flex items-center justify-between gap-2">
          <p className="text-[#1A1410] text-sm font-bold truncate">{video.title}</p>
          <div className="flex gap-1.5 shrink-0">
            <button
              onClick={goFullscreen}
              className="text-[11px] font-semibold px-3 py-1.5 rounded-lg border border-[#D4CFC9] text-[#1A1410] hover:border-[#3D4F5A] transition-colors"
            >
              ⛶ Tela cheia
            </button>
            <button
              onClick={onClose}
              className="text-[11px] font-semibold px-3 py-1.5 rounded-lg border border-[#D4CFC9] text-[#6B5E55] hover:text-[#1A1410] transition-colors"
            >
              ✕ Fechar
            </button>
          </div>
        </div>
        <div ref={playerRef} className="aspect-video w-full bg-[#1A1410]">
          {video.kind === "mp4" ? (
            <video src={video.url} controls autoPlay className="h-full w-full" />
          ) : (
            <iframe
              src={video.url}
              title={video.title}
              className="h-full w-full"
              allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share; fullscreen"
              allowFullScreen
            />
          )}
        </div>
      </div>
    </div>
  );
}

// ── Formulário (superadmin) ───────────────────────────────────────────────────

function VideoForm({ initial, onSaved, onCancel }: {
  initial: GuideVideo | null;
  onSaved: (v: GuideVideo) => void;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState(initial?.title ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [source, setSource] = useState(initial?.url ?? "");
  const [module, setModule] = useState(String(initial?.module ?? 1));
  const [order, setOrder] = useState(String(initial?.order ?? 1));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function save() {
    if (saving) return;
    setSaving(true);
    setError("");
    const mod = Math.max(1, parseInt(module, 10) || 1);
    const ord = Math.max(1, parseInt(order, 10) || 1);
    try {
      const v = initial
        ? await updateGuideVideo(initial.id, title, description, source, mod, ord)
        : await createGuideVideo(title, description, source, mod, ord);
      onSaved(v);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Erro ao salvar");
    } finally {
      setSaving(false);
    }
  }

  const inputCls = "w-full bg-[#F0EDE8] border border-[#D4CFC9] rounded-xl px-3 py-2 text-sm text-[#1A1410] placeholder:text-[#6B5E55]/60 focus:outline-none focus:border-[#3D4F5A]";

  return (
    <div className="bg-[#E8E4DF] border border-[#D4CFC9] rounded-2xl p-4 space-y-2">
      <p className="text-[#1A1410] text-sm font-bold">{initial ? "Editar vídeo" : "Adicionar vídeo"}</p>
      <input className={inputCls} placeholder="Título" value={title} onChange={e => setTitle(e.target.value)} />
      <input className={inputCls} placeholder="Descrição (opcional)" value={description} onChange={e => setDescription(e.target.value)} />
      <div className="flex gap-2">
        <label className="flex-1 text-[11px] text-[#6B5E55] font-semibold">
          Módulo
          <input className={`${inputCls} mt-1`} type="number" min={1} value={module} onChange={e => setModule(e.target.value)} />
        </label>
        <label className="flex-1 text-[11px] text-[#6B5E55] font-semibold">
          Ordem no módulo
          <input className={`${inputCls} mt-1`} type="number" min={1} value={order} onChange={e => setOrder(e.target.value)} />
        </label>
      </div>
      <textarea
        className={`${inputCls} min-h-[72px] font-mono text-xs`}
        placeholder='URL .mp4, URL de player ou <iframe ...> completo'
        value={source}
        onChange={e => setSource(e.target.value)}
      />
      <p className="text-[#6B5E55] text-[11px]">Aceita link .mp4 direto, URL de embed (YouTube/Vimeo/Adilo) ou o HTML do iframe inteiro.</p>
      {error && <p className="text-[#CC3300] text-xs font-semibold">{error}</p>}
      <div className="flex gap-2 pt-1">
        <button
          onClick={save}
          disabled={saving || !title.trim() || !source.trim()}
          className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-[#1A1410] text-[#F0EDE8] disabled:opacity-50"
        >
          {saving ? "Salvando..." : "Salvar"}
        </button>
        <button onClick={onCancel} className="text-xs font-semibold px-3 py-1.5 rounded-lg border border-[#D4CFC9] text-[#6B5E55]">
          Cancelar
        </button>
      </div>
    </div>
  );
}

// ── Página ────────────────────────────────────────────────────────────────────

export default function GuideVideosPage() {
  const [isSuperadmin, setIsSuperadmin] = useState(false);
  const [videos, setVideos] = useState<GuideVideo[]>([]);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<GuideVideo | null>(null);
  const [playing, setPlaying] = useState<GuideVideo | null>(null);

  useEffect(() => {
    listGuideVideos().then(setVideos).catch(() => {});
  }, []);

  useEffect(() => {
    fetch("/api/proxy/auth/me")
      .then(r => (r.ok ? r.json() : null))
      .then(data => {
        if (data?.funcao === "superadmin") setIsSuperadmin(true);
      })
      .catch(() => {});
  }, []);

  return (
    <div className="min-h-screen bg-[#F0EDE8]">

      {/* Header */}
      <PageHeaderShell maxWidthClassName="max-w-3xl">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-4">
            <Link href="/guide" className="mt-0.5 text-xs font-semibold uppercase tracking-[0.22em] text-[#6B5E55] transition-colors hover:text-[#1A1410]">
              Voltar
            </Link>
            <div>
              <h1 className="text-[#1A1410] font-bold text-lg leading-tight">Vídeos</h1>
              <p className="text-[#6B5E55] text-[12px] mt-0.5">Tutoriais em vídeo do KLEAD CRM</p>
            </div>
          </div>
          {isSuperadmin && !formOpen && (
            <button
              onClick={() => { setEditing(null); setFormOpen(true); }}
              className="text-xs font-semibold px-3 py-1.5 rounded-lg border border-[#D4CFC9] bg-[#E8E4DF] text-[#1A1410] hover:border-[#3D4F5A] transition-colors w-fit"
            >
              + Adicionar vídeo
            </button>
          )}
        </div>
      </PageHeaderShell>

      <main className="max-w-3xl mx-auto px-6 py-6 space-y-4">

        {formOpen && (
          <VideoForm
            initial={editing}
            onSaved={(v) => {
              setVideos(prev => {
                const i = prev.findIndex(p => p.id === v.id);
                if (i === -1) return [...prev, v];
                const next = [...prev];
                next[i] = v;
                return next;
              });
              setFormOpen(false);
              setEditing(null);
            }}
            onCancel={() => { setFormOpen(false); setEditing(null); }}
          />
        )}

        {videos.length > 0 && (() => {
          const sorted = [...videos].sort(
            (a, b) => (a.module ?? 1) - (b.module ?? 1) || (a.order ?? 1) - (b.order ?? 1)
          );
          const modules = [...new Set(sorted.map(v => v.module ?? 1))];
          return modules.map((mod) => {
            const items = sorted.filter(v => (v.module ?? 1) === mod);
            return (
              <section key={mod} className="space-y-3">
                <h2 className="text-[#1A1410] text-sm font-bold uppercase tracking-[0.18em] pt-2">
                  Módulo {mod}
                </h2>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {items.map((video, i) => (
                    <VideoCard
                      key={video.id}
                      video={video}
                      number={i + 1}
                      isSuperadmin={isSuperadmin}
                      onPlay={setPlaying}
                      onEdit={(v) => { setEditing(v); setFormOpen(true); }}
                      onDelete={(v) => {
                        if (!window.confirm(`Apagar o vídeo "${v.title}"?`)) return;
                        deleteGuideVideo(v.id)
                          .then(() => setVideos(prev => prev.filter(p => p.id !== v.id)))
                          .catch(() => {});
                      }}
                    />
                  ))}
                </div>
              </section>
            );
          });
        })()}

        {videos.length === 0 && (
          <div className="bg-[#E8E4DF] border border-[#D4CFC9] rounded-2xl p-8 text-center">
            <p className="text-[#6B5E55] text-sm">
              {isSuperadmin ? "Nenhum vídeo ainda. Clique em “+ Adicionar vídeo” para publicar o primeiro." : "Os vídeos tutoriais estarão disponíveis em breve."}
            </p>
          </div>
        )}

      </main>

      {playing && <PlayerModal video={playing} onClose={() => setPlaying(null)} />}
    </div>
  );
}
