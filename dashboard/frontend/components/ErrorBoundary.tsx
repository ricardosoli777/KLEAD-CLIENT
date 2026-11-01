"use client";

import { Component, type ReactNode } from "react";

type Props = { children: ReactNode };
type State = { error: Error | null };

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div className="min-h-screen bg-[#F0EDE8] flex items-center justify-center p-6">
          <div className="bg-[#E8E4DF] border border-[#D4CFC9] rounded-2xl p-8 max-w-md w-full text-center shadow-sm">
            <div className="w-12 h-12 rounded-full bg-[#2D0A0A] flex items-center justify-center mx-auto mb-4">
              <svg viewBox="0 0 24 24" className="w-6 h-6 fill-[#F87171]"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>
            </div>
            <h2 className="text-[#1A1410] font-bold text-lg mb-2">Algo deu errado</h2>
            <p className="text-[#6B5E55] text-sm mb-6">Ocorreu um erro inesperado. Recarregue a página para continuar.</p>
            <button
              onClick={() => window.location.reload()}
              className="bg-[#CC3300] hover:bg-[#991F00] text-[#F0EDE8] font-semibold px-6 py-2.5 rounded-xl text-sm transition-colors"
            >
              Recarregar página
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
