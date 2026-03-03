"use client";

import { useState, useEffect } from "react";
import { apiRequest } from "@/lib/api";
import AppLayout from "@/components/AppLayout";
import FilterBar from "@/components/FilterBar";
import { FastForward, Save, Info, AlertCircle, Pencil } from "lucide-react";
import styles from "./Classificar.module.css";
import CorrigirClassificacaoModal, {
  HabilidadeModulo,
} from "@/components/CorrigirClassificacaoModal";

export default function ClassificarPage() {
  const [questao, setQuestao] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const usuario =
    typeof window !== "undefined"
      ? JSON.parse(localStorage.getItem("usuario") || "{}")
      : null;
  const [area, setArea] = useState(usuario?.disciplina || "");
  const [disciplinaFiltro, setDisciplinaFiltro] = useState("");
  const [habilidadeFiltro, setHabilidadeFiltro] = useState("");
  const [saving, setSaving] = useState(false);
  const [skipping, setSkipping] = useState(false);
  const [observacao, setObservacao] = useState("");
  const [modulosSelecionados, setModulosSelecionados] = useState<any[]>([]);
  const [showCorrigirModal, setShowCorrigirModal] = useState(false);

  const fetchProxima = async (
    areaFiltro?: string,
    discFiltro?: string,
    habFiltro?: string,
  ) => {
    setLoading(true);
    setError("");
    setQuestao(null);
    setModulosSelecionados([]);
    setObservacao("");

    try {
      const query = new URLSearchParams();
      if (areaFiltro) query.append("area", areaFiltro);
      if (discFiltro) query.append("disciplina_id", discFiltro);
      if (habFiltro) query.append("habilidade_id", habFiltro);

      const params = query.toString() ? `?${query.toString()}` : "";
      const data = await apiRequest(`/proxima${params}`);
      setQuestao(data);
    } catch (err: any) {
      setError(err.message || "Nenhuma questão encontrada com esses filtros.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchProxima(area, disciplinaFiltro, habilidadeFiltro);
  }, [area, disciplinaFiltro, habilidadeFiltro]);

  const toggleModulo = (modulo: any) => {
    setModulosSelecionados((prev) => {
      const isSelected = prev.some((m) => m.id === modulo.id);
      if (isSelected) {
        return prev.filter((m) => m.id !== modulo.id);
      } else {
        return [...prev, modulo];
      }
    });
  };

  const handleSalvar = async () => {
    if (modulosSelecionados.length === 0) return;
    setSaving(true);

    try {
      await apiRequest("/salvar", {
        method: "POST",
        body: JSON.stringify({
          questao_id: questao.id,
          // Campos múltiplos (novos)
          habilidade_modulo_ids: modulosSelecionados.map((m) => m.id),
          modulos_escolhidos: modulosSelecionados.map((m) => m.modulo),
          classificacoes_trieduc: modulosSelecionados.map(
            (m) => m.habilidade_descricao,
          ),
          descricoes_assunto: modulosSelecionados.map((m) => m.descricao),
          // Campos legados (primeiro selecionado para retrocompatibilidade)
          habilidade_modulo_id: modulosSelecionados[0].id,
          modulo_escolhido: modulosSelecionados[0].modulo,
          classificacao_trieduc: modulosSelecionados[0].habilidade_descricao,
          descricao_assunto: modulosSelecionados[0].descricao,
          tipo_acao: "classificacao_nova",
          observacao,
        }),
      });
      fetchProxima(area, disciplinaFiltro, habilidadeFiltro);
    } catch (err: any) {
      alert(err.message);
    } finally {
      setSaving(false);
    }
  };

  const handleSalvarCorrecao = async (modulosCorrecao: HabilidadeModulo[]) => {
    if (modulosCorrecao.length === 0) return;
    setSaving(true);
    try {
      await apiRequest("/salvar", {
        method: "POST",
        body: JSON.stringify({
          questao_id: questao.id,
          habilidade_modulo_ids: modulosCorrecao.map((m) => m.id),
          modulos_escolhidos: modulosCorrecao.map((m) => m.modulo),
          classificacoes_trieduc: modulosCorrecao.map(
            (m) => m.habilidade_descricao,
          ),
          descricoes_assunto: modulosCorrecao.map((m) => m.descricao),
          habilidade_modulo_id: modulosCorrecao[0].id,
          modulo_escolhido: modulosCorrecao[0].modulo,
          classificacao_trieduc: modulosCorrecao[0].habilidade_descricao,
          descricao_assunto: modulosCorrecao[0].descricao,
          tipo_acao: "correcao",
          observacao,
        }),
      });
      setShowCorrigirModal(false);
      fetchProxima(area, disciplinaFiltro, habilidadeFiltro);
    } catch (err: any) {
      alert(err.message);
    } finally {
      setSaving(false);
    }
  };

  const handlePular = async () => {
    if (!questao) return;
    setSkipping(true);

    try {
      await apiRequest("/pular", {
        method: "POST",
        body: JSON.stringify({ questao_id: questao.id }),
      });
      fetchProxima(area, disciplinaFiltro, habilidadeFiltro);
    } catch (err: any) {
      alert(err.message);
    } finally {
      setSkipping(false);
    }
  };

  return (
    <AppLayout>
      <div className={styles.header}>
        <div className={styles.headerInfo}>
          <h1>Classificação Manual</h1>
          <p>Associe a questão ao módulo educacional correto</p>
        </div>
      </div>

      <FilterBar
        onFilterChange={(a, d, h) => {
          setArea(a);
          setDisciplinaFiltro(d);
          setHabilidadeFiltro(h);
        }}
      />

      {loading ? (
        <div className={styles.loading}>
          <div className={styles.spinner}></div>
          <span>Carregando próxima questão...</span>
        </div>
      ) : error ? (
        <div className={styles.empty}>
          <AlertCircle size={48} color="var(--primary)" />
          <p>{error}</p>
          <button
            onClick={() =>
              fetchProxima(area, disciplinaFiltro, habilidadeFiltro)
            }
          >
            Tentar Novamente
          </button>
        </div>
      ) : (
        questao && (
          <div className={styles.content}>
            <div className={`${styles.questaoCard} glass fade-in`}>
              <div className={styles.questaoMeta}>
                <span className={styles.tag}>{questao.disciplina_nome}</span>
                <span className={styles.habTag}>
                  {questao.habilidade_descricao}
                </span>
                <span className={styles.idTag}>ID: {questao.id}</span>
              </div>

              {questao.texto_base && (
                <div
                  className={styles.textoBase}
                  dangerouslySetInnerHTML={{
                    __html: questao.texto_base_html || questao.texto_base,
                  }}
                />
              )}

              <div
                className={styles.enunciado}
                dangerouslySetInnerHTML={{
                  __html: questao.enunciado_html || questao.enunciado,
                }}
              />

              {questao.alternativas && questao.alternativas.length > 0 && (
                <div className={styles.alternativas}>
                  {questao.alternativas.map((alt: any, index: number) => (
                    <div
                      key={index}
                      className={`${styles.altItem} ${alt.correta ? styles.altCorreta : ""}`}
                    >
                      <span className={styles.altLetra}>
                        {String.fromCharCode(97 + index)})
                      </span>
                      <span
                        dangerouslySetInnerHTML={{
                          __html: alt.conteudo_html || alt.conteudo,
                        }}
                      />
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className={`${styles.moduloCard} glass fade-in`}>
              <div className={styles.moduloHeader}>
                <Info size={18} />
                <h3>Sugestões de Módulos</h3>
              </div>
              <p className={styles.moduloHint}>
                Selecione um ou mais módulos adequados
                {modulosSelecionados.length > 0 && (
                  <span className={styles.selectedCount}>
                    {modulosSelecionados.length} selecionado
                    {modulosSelecionados.length > 1 ? "s" : ""}
                  </span>
                )}
              </p>

              <div className={styles.moduloList}>
                {questao.modulos_possiveis.map((m: any) => (
                  <label
                    key={m.id}
                    className={`${styles.moduloItem} ${modulosSelecionados.some((s) => s.id === m.id) ? styles.moduloSelected : ""}`}
                  >
                    <input
                      type="checkbox"
                      name="modulo"
                      checked={modulosSelecionados.some((s) => s.id === m.id)}
                      onChange={() => toggleModulo(m)}
                    />
                    <div className={styles.moduloText}>
                      <strong>{m.modulo}</strong>
                      <span>{m.descricao}</span>
                    </div>
                  </label>
                ))}
              </div>

              <div className={styles.corrigirBtnWrap}>
                <button
                  className={styles.corrigirBtn}
                  onClick={() => setShowCorrigirModal(true)}
                  disabled={saving}
                >
                  <Pencil size={15} />
                  Corrigir classificação
                </button>
              </div>

              <div className={styles.actionArea}>
                <textarea
                  placeholder="Descreva brevemente o motivo da classificação"
                  value={observacao}
                  onChange={(e) => setObservacao(e.target.value)}
                />
                <div className={styles.buttons}>
                  <button
                    onClick={handlePular}
                    disabled={skipping}
                    className={styles.skipBtn}
                  >
                    <FastForward size={18} />
                    {skipping ? "Pulando..." : "Pular"}
                  </button>
                  <button
                    onClick={handleSalvar}
                    disabled={modulosSelecionados.length === 0 || saving}
                    className={styles.saveBtn}
                  >
                    <Save size={18} />
                    {saving ? "Gravando..." : "Salvar Classificação"}
                  </button>
                </div>
              </div>
            </div>
          </div>
        )
      )}
      <CorrigirClassificacaoModal
        isOpen={showCorrigirModal}
        onClose={() => setShowCorrigirModal(false)}
        onConfirmar={handleSalvarCorrecao}
        saving={saving}
      />
    </AppLayout>
  );
}
