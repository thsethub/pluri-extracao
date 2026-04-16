"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { createPortal } from "react-dom";
import {
  ZoomIn,
  X,
  ImageOff,
  Plus,
  Minus,
  RotateCcw,
  Loader,
} from "lucide-react";
import styles from "./OcrImageViewer.module.css";

interface OcrImageViewerProps {
  arquivo: string | null;
  textoOcr?: string | null;
}

const MIN_SCALE = 1;
const MAX_SCALE = 5;
const SCALE_STEP = 0.4;

export default function OcrImageViewer({
  arquivo,
  textoOcr,
}: OcrImageViewerProps) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [erro, setErro] = useState(false);
  const [zoomAberto, setZoomAberto] = useState(false);

  // Estado de pan+zoom dentro do modal
  const [scale, setScale] = useState(MIN_SCALE);
  const [position, setPosition] = useState({ x: 0, y: 0 });
  const isDragging = useRef(false);
  const lastMousePos = useRef({ x: 0, y: 0 });

  // Busca a imagem via proxy autenticado para não expor a URL real
  useEffect(() => {
    if (!arquivo) return;

    setLoading(true);
    setErro(false);

    const token =
      typeof window !== "undefined" ? localStorage.getItem("token") : null;
    const url = `/api-proxy/classificacao/ocr-confianca/imagem?arquivo=${encodeURIComponent(arquivo)}`;

    fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      .then((r) => {
        if (!r.ok) throw new Error();
        return r.blob();
      })
      .then((blob) => setBlobUrl(URL.createObjectURL(blob)))
      .catch(() => setErro(true))
      .finally(() => setLoading(false));

    // Revoga o blob URL anterior ao trocar de imagem
    return () => {
      setBlobUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return null;
      });
    };
  }, [arquivo]);

  const resetZoom = useCallback(() => {
    setScale(MIN_SCALE);
    setPosition({ x: 0, y: 0 });
  }, []);

  const handleAbrir = () => {
    resetZoom();
    setZoomAberto(true);
  };

  const handleFechar = () => {
    setZoomAberto(false);
    resetZoom();
  };

  // Zoom com scroll do mouse
  const handleWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    setScale((s) => {
      const next = s - e.deltaY * 0.005;
      return Math.min(MAX_SCALE, Math.max(MIN_SCALE, next));
    });
  };

  // Início do drag (só funciona se estiver com zoom)
  const handleMouseDown = (e: React.MouseEvent) => {
    if (scale <= MIN_SCALE) return;
    isDragging.current = true;
    lastMousePos.current = { x: e.clientX, y: e.clientY };
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!isDragging.current) return;
    const dx = e.clientX - lastMousePos.current.x;
    const dy = e.clientY - lastMousePos.current.y;
    lastMousePos.current = { x: e.clientX, y: e.clientY };
    setPosition((p) => ({ x: p.x + dx, y: p.y + dy }));
  };

  const handleMouseUp = () => {
    isDragging.current = false;
  };

  const zoomIn = () => setScale((s) => Math.min(MAX_SCALE, s + SCALE_STEP));
  const zoomOut = () => {
    setScale((s) => {
      const next = Math.max(MIN_SCALE, s - SCALE_STEP);
      // Reseta posição ao voltar ao tamanho original
      if (next === MIN_SCALE) setPosition({ x: 0, y: 0 });
      return next;
    });
  };

  // --- Render ---

  if (!arquivo) {
    return (
      <div className={styles.semImagem}>
        <ImageOff size={36} />
        <p>Sem imagem associada</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className={styles.semImagem}>
        <Loader size={28} className={styles.spinner} />
        <p>Carregando imagem...</p>
      </div>
    );
  }

  if (erro || !blobUrl) {
    return (
      <div className={styles.semImagem}>
        <ImageOff size={36} />
        <p>Não foi possível carregar a imagem</p>
      </div>
    );
  }

  const cursorStyle =
    scale > MIN_SCALE ? (isDragging.current ? "grabbing" : "grab") : "zoom-in";

  return (
    <>
      <div className={styles.container}>
        <img src={blobUrl} alt="Imagem da redação" className={styles.img} />
        <button
          className={styles.btnZoom}
          onClick={handleAbrir}
          title="Ampliar imagem"
        >
          <ZoomIn size={18} />
        </button>
      </div>

      {zoomAberto &&
        createPortal(
          <div
            className={styles.overlay}
            onMouseUp={handleMouseUp}
            onMouseLeave={handleMouseUp}
          >
            {/* Controles */}
            <div className={styles.controles}>
              <button
                onClick={zoomOut}
                disabled={scale <= MIN_SCALE}
                title="Reduzir"
              >
                <Minus size={16} />
              </button>
              <span className={styles.scaleLabel}>
                {Math.round(scale * 100)}%
              </span>
              <button
                onClick={zoomIn}
                disabled={scale >= MAX_SCALE}
                title="Ampliar"
              >
                <Plus size={16} />
              </button>
              <button
                onClick={resetZoom}
                disabled={scale === MIN_SCALE}
                title="Resetar zoom"
              >
                <RotateCcw size={16} />
              </button>
            </div>

            <button
              className={styles.btnFechar}
              onClick={handleFechar}
              title="Fechar"
            >
              <X size={20} />
            </button>

            <div
              className={
                textoOcr ? styles.splitContainer : styles.soloContainer
              }
            >
              {/* Área da imagem com pan+zoom */}
              <div
                className={styles.zoomArea}
                onWheel={handleWheel}
                onMouseDown={handleMouseDown}
                onMouseMove={handleMouseMove}
                style={{ cursor: cursorStyle }}
              >
                <img
                  src={blobUrl}
                  alt="Imagem da redação ampliada"
                  className={styles.imgZoom}
                  style={{
                    transform: `scale(${scale}) translate(${position.x / scale}px, ${position.y / scale}px)`,
                  }}
                  draggable={false}
                />
              </div>

              {/* Painel de texto OCR (só no split-view) */}
              {textoOcr && (
                <div className={styles.textoPanel}>
                  <h4 className={styles.textoPanelTitle}>
                    Texto Extraído pelo OCR
                  </h4>
                  <div className={styles.textoPanelContent}>{textoOcr}</div>
                </div>
              )}
            </div>

            <p className={styles.dica}>Scroll para zoom · Arraste para mover</p>
          </div>,
          document.body,
        )}
    </>
  );
}
