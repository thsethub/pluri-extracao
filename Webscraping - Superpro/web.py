"""
Interface web para controle do agente de extração.
Roda na porta 8501 e se comunica com a API local (porta 8000).

Uso:
    python web.py
"""

import asyncio
import json
import subprocess
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import os

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from loguru import logger

app = FastAPI(title="Painel de Extração - SuperProfessor")

# ── Estado global ────────────────────────────────────────────────────
_state = {
    "running": False,
    "process": None,
    "started_at": None,
    "disc_ids": [],
    "max_questions": 0,
}
_logs: deque[str] = deque(maxlen=500)

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
logger.info(f"API_BASE configurado: {API_BASE}")


# ── Endpoints de dados (proxy para API local) ───────────────────────


@app.get("/api/disciplinas")
async def get_disciplinas():
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{API_BASE}/db/disciplinas")
        return r.json()


@app.get("/api/stats")
async def get_stats():
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.get(f"{API_BASE}/extracao/stats", params={"ano_id": 3})
        return r.json()


@app.delete("/api/reset")
async def reset_db():
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.delete(f"{API_BASE}/extracao/reset")
        return r.json()


@app.get("/api/conferencia")
async def get_conferencia(
    page: int = 1,
    per_page: int = 20,
    disciplina_id: int | None = None,
    apenas_extraidas: bool = True,
    precisa_verificar: bool | None = None,
    tem_classificacao: bool | None = None,
    questao_id: int | None = None,
    superpro_id: int | None = None,
    data_inicio: str | None = None,
    data_fim: str | None = None,
):
    """Proxy para listar assuntos extraídos com filtros."""
    params = {
        "page": page,
        "per_page": per_page,
        "apenas_extraidas": apenas_extraidas,
    }
    if disciplina_id:
        params["disciplina_id"] = disciplina_id
    if precisa_verificar is not None:
        params["precisa_verificar"] = precisa_verificar
    if tem_classificacao is not None:
        params["tem_classificacao"] = tem_classificacao
    if questao_id:
        params["questao_id"] = questao_id
    if superpro_id:
        params["superpro_id"] = superpro_id
    if data_inicio:
        params["data_inicio"] = data_inicio
    if data_fim:
        params["data_fim"] = data_fim

    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.get(f"{API_BASE}/extracao/assuntos", params=params)
        return r.json()


# ── Controle do agente ───────────────────────────────────────────────


@app.post("/api/start")
async def start_agent(request: Request):
    if _state["running"]:
        return {"ok": False, "error": "Agente já está rodando"}

    body = await request.json()
    disc_ids = body.get("disc_ids", [])
    max_q = body.get("max_questions", 0)

    if not disc_ids:
        return {"ok": False, "error": "Selecione pelo menos uma disciplina"}

    # Montar comando
    cmd = [
        sys.executable,
        "main.py",
        "--run",
        "--disc",
        *[str(d) for d in disc_ids],
    ]
    if max_q and max_q > 0:
        cmd.extend(["--max", str(max_q)])

    _logs.clear()
    _logs.append(
        f"[{_now()}] Iniciando extração: disciplinas={disc_ids} max={max_q or '∞'}"
    )

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(Path(__file__).parent),
        bufsize=1,
    )

    _state["running"] = True
    _state["process"] = proc
    _state["started_at"] = time.time()
    _state["disc_ids"] = disc_ids
    _state["max_questions"] = max_q

    # Ler output em background
    asyncio.get_event_loop().run_in_executor(None, _read_output, proc)

    return {"ok": True, "pid": proc.pid}


@app.post("/api/stop")
async def stop_agent():
    if not _state["running"] or not _state["process"]:
        return {"ok": False, "error": "Agente não está rodando"}

    proc = _state["process"]
    proc.terminate()
    _logs.append(f"[{_now()}] ⏹ Parando agente (PID {proc.pid})...")

    # Aguardar encerramento
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()

    _state["running"] = False
    _state["process"] = None
    _logs.append(f"[{_now()}] Agente parado.")

    return {"ok": True}


@app.get("/api/status")
async def get_status():
    elapsed = ""
    if _state["started_at"] and _state["running"]:
        delta = time.time() - _state["started_at"]
        h, rem = divmod(int(delta), 3600)
        m, s = divmod(rem, 60)
        elapsed = f"{h}h{m:02d}m{s:02d}s"

    return {
        "running": _state["running"],
        "elapsed": elapsed,
        "disc_ids": _state["disc_ids"],
        "max_questions": _state["max_questions"],
    }


@app.get("/api/logs")
async def get_logs(after: int = 0):
    all_logs = list(_logs)
    return {"logs": all_logs[after:], "total": len(all_logs)}


# ── SSE para logs em tempo real ──────────────────────────────────────


@app.get("/api/logs/stream")
async def stream_logs():
    async def event_generator():
        idx = 0
        while True:
            all_logs = list(_logs)
            if idx < len(all_logs):
                for line in all_logs[idx:]:
                    yield f"data: {json.dumps({'log': line})}\n\n"
                idx = len(all_logs)

            if not _state["running"] and idx >= len(all_logs):
                yield f"data: {json.dumps({'done': True})}\n\n"
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ── Página HTML ──────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_PAGE


@app.get("/conferencia", response_class=HTMLResponse)
async def conferencia_page():
    return HTML_CONFERENCIA


# ── Helpers ──────────────────────────────────────────────────────────


def _now():
    return datetime.now().strftime("%H:%M:%S")


def _read_output(proc: subprocess.Popen):
    """Lê stdout do processo em thread separada."""
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                _logs.append(line)
                # Exibir no stdout do container para visibilidade nos logs
                print(line, flush=True)
    except Exception:
        pass
    finally:
        proc.wait()
        msg = f"[{_now()}] Processo encerrado (exit code: {proc.returncode})"
        _logs.append(msg)
        print(msg, flush=True)
        _state["running"] = False
        _state["process"] = None


# ── HTML inline ──────────────────────────────────────────────────────

HTML_PAGE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Extração SuperProfessor</title>
<style>
  :root {
    --bg: #0f1117;
    --card: #1a1d27;
    --border: #2a2d3a;
    --text: #e4e4e7;
    --muted: #8b8d97;
    --accent: #6366f1;
    --accent-hover: #818cf8;
    --green: #22c55e;
    --red: #ef4444;
    --yellow: #eab308;
    --cyan: #06b6d4;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
  }
  .container { max-width: 1100px; margin: 0 auto; padding: 24px; }

  header {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--border);
  }
  header h1 { font-size: 22px; font-weight: 600; }
  header h1 span { color: var(--accent); }
  #status-badge {
    padding: 6px 14px; border-radius: 20px; font-size: 13px; font-weight: 500;
  }
  .badge-idle { background: var(--border); color: var(--muted); }
  .badge-running { background: rgba(34,197,94,.15); color: var(--green); animation: pulse 2s infinite; }

  @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:.6; } }

  /* ── Cards ── */
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 20px; }
  .card {
    background: var(--card); border: 1px solid var(--border);
    border-radius: 10px; padding: 20px;
  }
  .card h2 { font-size: 14px; color: var(--muted); text-transform: uppercase; letter-spacing: .5px; margin-bottom: 12px; }

  /* ── Tabela disciplinas ── */
  .disc-table { width: 100%; border-collapse: collapse; font-size: 13px; }
  .disc-table th {
    text-align: left; padding: 8px 10px; color: var(--muted); font-weight: 500;
    border-bottom: 1px solid var(--border); font-size: 11px; text-transform: uppercase;
  }
  .disc-table td { padding: 7px 10px; border-bottom: 1px solid rgba(255,255,255,.04); }
  .disc-table tr:hover { background: rgba(99,102,241,.06); }
  .disc-table input[type="checkbox"] { accent-color: var(--accent); transform: scale(1.1); cursor: pointer; }
  .disc-table .num { text-align: right; font-variant-numeric: tabular-nums; }
  .disc-table .no-sp { color: var(--muted); opacity: .5; }
  .check-all { cursor: pointer; }

  /* ── Controles ── */
  .controls { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  .btn {
    padding: 10px 24px; border: none; border-radius: 8px; font-size: 14px;
    font-weight: 600; cursor: pointer; transition: all .15s;
  }
  .btn-start { background: var(--accent); color: #fff; }
  .btn-start:hover { background: var(--accent-hover); }
  .btn-start:disabled { opacity: .4; cursor: not-allowed; }
  .btn-stop { background: var(--red); color: #fff; }
  .btn-stop:hover { background: #dc2626; }
  .btn-stop:disabled { opacity: .4; cursor: not-allowed; }

  .input-max {
    background: var(--bg); border: 1px solid var(--border); color: var(--text);
    padding: 9px 14px; border-radius: 8px; width: 150px; font-size: 14px;
  }
  .input-max:focus { outline: none; border-color: var(--accent); }
  .input-max::placeholder { color: var(--muted); }

  /* ── Stats resumo ── */
  .stats-row { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 16px; }
  .stat-item {
    background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
    padding: 14px 18px; flex: 1; min-width: 120px; text-align: center;
  }
  .stat-item .value { font-size: 24px; font-weight: 700; }
  .stat-item .label { font-size: 11px; color: var(--muted); text-transform: uppercase; margin-top: 4px; }
  .stat-green .value { color: var(--green); }
  .stat-yellow .value { color: var(--yellow); }
  .stat-red .value { color: var(--red); }
  .stat-cyan .value { color: var(--cyan); }

  /* ── Progress bar ── */
  .progress-bar {
    width: 100%; height: 8px; background: var(--border); border-radius: 4px;
    overflow: hidden; margin-bottom: 20px;
  }
  .progress-fill {
    height: 100%; background: linear-gradient(90deg, var(--accent), var(--cyan));
    border-radius: 4px; transition: width .5s;
  }

  /* ── Console de logs ── */
  .log-card { grid-column: 1 / -1; }
  #log-console {
    background: #0a0c10; border: 1px solid var(--border); border-radius: 8px;
    padding: 14px; height: 340px; overflow-y: auto; font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace;
    font-size: 12px; line-height: 1.7; color: #a1a1aa;
  }
  #log-console .log-success { color: var(--green); }
  #log-console .log-warning { color: var(--yellow); }
  #log-console .log-error { color: var(--red); }
  #log-console .log-info { color: var(--cyan); }
  #log-console .log-match { color: #a78bfa; font-weight: 500; }

  .elapsed { color: var(--muted); font-size: 13px; }
</style>
</head>
<body>
<div class="container">

  <header>
    <h1>🤖 <span>Extração</span> SuperProfessor</h1>
    <div>
      <a href="/conferencia" style="color:var(--accent); text-decoration:none; font-size:13px; margin-right:8px; padding:5px 12px; border:1px solid var(--accent); border-radius:6px;">🔎 Conferência</a>
      <a href="/verificacao" style="color:var(--orange,#f97316); text-decoration:none; font-size:13px; margin-right:16px; padding:5px 12px; border:1px solid var(--orange,#f97316); border-radius:6px;">🔄 Verificação</a>
      <span class="elapsed" id="elapsed"></span>
      <span id="status-badge" class="badge-idle">Parado</span>
    </div>
  </header>

  <!-- Stats resumo -->
  <div class="stats-row" id="stats-row">
    <div class="stat-item"><div class="value" id="st-total">—</div><div class="label">Total</div></div>
    <div class="stat-item stat-green"><div class="value" id="st-extraidas">—</div><div class="label">Extraídas</div></div>
    <div class="stat-item stat-yellow"><div class="value" id="st-imagem">—</div><div class="label">Com Imagem</div></div>
    <div class="stat-item stat-red"><div class="value" id="st-pendentes">—</div><div class="label">Pendentes</div></div>
    <div class="stat-item stat-cyan"><div class="value" id="st-pct">—</div><div class="label">Progresso</div></div>
  </div>
  <div class="progress-bar"><div class="progress-fill" id="progress-fill" style="width:0%"></div></div>

  <div class="grid">
    <!-- Disciplinas -->
    <div class="card">
      <h2>Disciplinas</h2>
      <table class="disc-table">
        <thead>
          <tr>
            <th><input type="checkbox" class="check-all" id="check-all" title="Selecionar todas"></th>
            <th>ID</th>
            <th>Disciplina</th>
            <th style="text-align:right">Total</th>
            <th style="text-align:right">Feitas</th>
            <th style="text-align:right">Pend.</th>
          </tr>
        </thead>
        <tbody id="disc-body"></tbody>
      </table>
    </div>

    <!-- Controles -->
    <div class="card">
      <h2>Controles</h2>
      <div style="margin-bottom: 20px;">
        <label style="font-size:13px; color:var(--muted); display:block; margin-bottom:6px;">Limite de questões (0 = sem limite)</label>
        <input type="number" class="input-max" id="max-questions" value="0" min="0" placeholder="0 = infinito">
      </div>
      <div class="controls">
        <button class="btn btn-start" id="btn-start" onclick="startAgent()">▶ Iniciar Extração</button>
        <button class="btn btn-stop" id="btn-stop" onclick="stopAgent()" disabled>⏹ Parar</button>
      </div>
      <div id="ctrl-msg" style="margin-top:14px; font-size:13px; min-height:20px;"></div>

      <div style="margin-top: 30px; padding-top: 16px; border-top: 1px solid var(--border);">
        <h2 style="margin-bottom:12px;">Seleção Rápida</h2>
        <div style="display:flex; gap:8px; flex-wrap:wrap;">
          <button class="btn" style="background:var(--border); color:var(--text); padding:6px 14px; font-size:12px;" onclick="selectGroup('big')">Grandes (1k+)</button>
          <button class="btn" style="background:var(--border); color:var(--text); padding:6px 14px; font-size:12px;" onclick="selectGroup('small')">Pequenas (&lt;1k)</button>
          <button class="btn" style="background:var(--border); color:var(--text); padding:6px 14px; font-size:12px;" onclick="selectGroup('none')">Nenhuma</button>
        </div>
      </div>

      <div style="margin-top: 20px; padding-top: 16px; border-top: 1px solid var(--border);">
        <h2 style="margin-bottom:12px;">Manutenção</h2>
        <button class="btn" style="background:var(--red); color:#fff; padding:8px 18px; font-size:13px;" onclick="resetDB()" id="btn-reset">🗑️ Resetar Banco</button>
        <div id="reset-msg" style="margin-top:8px; font-size:12px; min-height:16px;"></div>
      </div>
    </div>

    <!-- Logs -->
    <div class="card log-card">
      <h2>Console</h2>
      <div id="log-console"><span style="color:var(--muted)">Aguardando início...</span></div>
    </div>
  </div>

</div>

<script>
const SP_MAP = {1:22,2:1,5:9,6:10,7:2,8:3,9:4,10:5,11:7,12:6,14:8,15:11};
let statsData = [];
let logIndex = 0;
let polling = null;
let isRunning = false;

// ── Init ──
async function init() {
  await loadStats();
  checkStatus();
  setInterval(loadStats, 15000);
  setInterval(checkStatus, 3000);
}

// ── Carregar stats e disciplinas ──
async function loadStats() {
  try {
    const r = await fetch('/api/stats');
    statsData = await r.json();
    renderDisciplinas(statsData);
    renderSummary(statsData);
  } catch(e) { console.error('Stats error:', e); }
}

function renderDisciplinas(data) {
  const tbody = document.getElementById('disc-body');
  const checked = new Set([...tbody.querySelectorAll('input:checked')].map(i => +i.value));

  tbody.innerHTML = data.map(d => {
    const hasSP = d.disciplina_id in SP_MAP;
    const cls = hasSP ? '' : 'no-sp';
    const ch = checked.has(d.disciplina_id) || (!checked.size && hasSP) ? '' : '';
    return `<tr class="${cls}">
      <td><input type="checkbox" value="${d.disciplina_id}" ${hasSP ? '' : 'disabled title="Sem mapeamento no SuperProfessor"'} ${checked.has(d.disciplina_id) ? 'checked' : ''}></td>
      <td>${d.disciplina_id}</td>
      <td>${d.disciplina_nome}</td>
      <td class="num">${fmt(d.total_questoes)}</td>
      <td class="num" style="color:var(--green)">${fmt(d.extraidas)}</td>
      <td class="num" style="color:${d.pendentes > 0 ? 'var(--red)' : 'var(--green)'}">${fmt(d.pendentes)}</td>
    </tr>`;
  }).join('');
}

function renderSummary(data) {
  const valid = data.filter(d => d.disciplina_id in SP_MAP);
  const total = valid.reduce((s,d) => s + d.total_questoes, 0);
  const extraidas = valid.reduce((s,d) => s + d.extraidas, 0);
  const imagem = valid.reduce((s,d) => s + d.com_imagem, 0);
  const pendentes = valid.reduce((s,d) => s + d.pendentes, 0);
  const pct = total > 0 ? ((extraidas + imagem) / total * 100) : 0;

  document.getElementById('st-total').textContent = fmt(total);
  document.getElementById('st-extraidas').textContent = fmt(extraidas);
  document.getElementById('st-imagem').textContent = fmt(imagem);
  document.getElementById('st-pendentes').textContent = fmt(pendentes);
  document.getElementById('st-pct').textContent = pct.toFixed(1) + '%';
  document.getElementById('progress-fill').style.width = pct + '%';
}

// ── Seleção ──
document.getElementById('check-all').addEventListener('change', function() {
  document.querySelectorAll('#disc-body input[type=checkbox]:not(:disabled)').forEach(cb => cb.checked = this.checked);
});

function selectGroup(type) {
  document.querySelectorAll('#disc-body input[type=checkbox]:not(:disabled)').forEach(cb => {
    const d = statsData.find(s => s.disciplina_id === +cb.value);
    if (type === 'big') cb.checked = d && d.total_questoes >= 1000;
    else if (type === 'small') cb.checked = d && d.total_questoes > 0 && d.total_questoes < 1000;
    else cb.checked = false;
  });
}

// ── Controle do agente ──
async function startAgent() {
  const selected = [...document.querySelectorAll('#disc-body input:checked')].map(i => +i.value);
  if (!selected.length) { showMsg('Selecione pelo menos uma disciplina', 'var(--red)'); return; }

  const maxQ = +document.getElementById('max-questions').value || 0;

  document.getElementById('btn-start').disabled = true;
  showMsg('Iniciando...', 'var(--yellow)');

  try {
    const r = await fetch('/api/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ disc_ids: selected, max_questions: maxQ })
    });
    const data = await r.json();
    if (data.ok) {
      showMsg(`Agente iniciado (PID ${data.pid})`, 'var(--green)');
      setRunning(true);
      logIndex = 0;
      document.getElementById('log-console').innerHTML = '';
      startLogPolling();
    } else {
      showMsg(data.error, 'var(--red)');
      document.getElementById('btn-start').disabled = false;
    }
  } catch(e) {
    showMsg('Erro ao conectar', 'var(--red)');
    document.getElementById('btn-start').disabled = false;
  }
}

async function stopAgent() {
  document.getElementById('btn-stop').disabled = true;
  showMsg('Parando...', 'var(--yellow)');

  try {
    const r = await fetch('/api/stop', { method: 'POST' });
    const data = await r.json();
    showMsg(data.ok ? 'Agente parado' : data.error, data.ok ? 'var(--green)' : 'var(--red)');
  } catch(e) { showMsg('Erro', 'var(--red)'); }
}

// ── Status polling ──
async function checkStatus() {
  try {
    const r = await fetch('/api/status');
    const data = await r.json();
    const badge = document.getElementById('status-badge');
    const elapsed = document.getElementById('elapsed');

    if (data.running) {
      badge.textContent = 'Rodando';
      badge.className = 'badge-running';
      elapsed.textContent = data.elapsed;
      if (!isRunning) { setRunning(true); startLogPolling(); }
    } else {
      badge.textContent = 'Parado';
      badge.className = 'badge-idle';
      elapsed.textContent = '';
      if (isRunning) setRunning(false);
    }
  } catch(e) {}
}

function setRunning(v) {
  isRunning = v;
  document.getElementById('btn-start').disabled = v;
  document.getElementById('btn-stop').disabled = !v;
  if (!v && polling) { clearInterval(polling); polling = null; }
}

// ── Log polling ──
function startLogPolling() {
  if (polling) clearInterval(polling);
  polling = setInterval(pollLogs, 800);
}

async function pollLogs() {
  try {
    const r = await fetch(`/api/logs?after=${logIndex}`);
    const data = await r.json();
    if (data.logs.length) {
      const console_ = document.getElementById('log-console');
      data.logs.forEach(line => {
        const div = document.createElement('div');
        div.className = getLogClass(line);
        div.textContent = line;
        console_.appendChild(div);
      });
      console_.scrollTop = console_.scrollHeight;
      logIndex = data.total;
    }
  } catch(e) {}
}

function getLogClass(line) {
  if (line.includes('SUCCESS') || line.includes('✅') || line.includes('MATCH')) return 'log-match';
  if (line.includes('WARNING') || line.includes('⚠') || line.includes('Retry')) return 'log-warning';
  if (line.includes('ERROR') || line.includes('❌')) return 'log-error';
  if (line.includes('INFO')) return 'log-info';
  return '';
}

// ── Reset DB ──
async function resetDB() {
  if (!confirm('⚠️ Tem certeza? Isso vai APAGAR todos os registros de extração!')) return;
  if (isRunning) { showMsg('Pare o agente antes de resetar', 'var(--red)'); return; }

  document.getElementById('btn-reset').disabled = true;
  document.getElementById('reset-msg').textContent = 'Resetando...';
  document.getElementById('reset-msg').style.color = 'var(--yellow)';

  try {
    const r = await fetch('/api/reset', { method: 'DELETE' });
    const data = await r.json();
    if (data.success) {
      document.getElementById('reset-msg').textContent = `✅ ${data.deleted} registros removidos`;
      document.getElementById('reset-msg').style.color = 'var(--green)';
      await loadStats();
    } else {
      document.getElementById('reset-msg').textContent = '❌ Erro ao resetar';
      document.getElementById('reset-msg').style.color = 'var(--red)';
    }
  } catch(e) {
    document.getElementById('reset-msg').textContent = '❌ Erro de conexão';
    document.getElementById('reset-msg').style.color = 'var(--red)';
  } finally {
    document.getElementById('btn-reset').disabled = false;
  }
}

// ── Helpers ──
function fmt(n) { return n != null ? n.toLocaleString('pt-BR') : '—'; }
function showMsg(msg, color) {
  const el = document.getElementById('ctrl-msg');
  el.textContent = msg;
  el.style.color = color || 'var(--text)';
}

init();
</script>
</body>
</html>
"""


# ── HTML da Página de Conferência ────────────────────────────────────

HTML_CONFERENCIA = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Conferência — Extração SuperProfessor</title>
<style>
  :root {
    --bg: #0f1117;
    --card: #1a1d27;
    --border: #2a2d3a;
    --text: #e4e4e7;
    --muted: #8b8d97;
    --accent: #6366f1;
    --accent-hover: #818cf8;
    --green: #22c55e;
    --red: #ef4444;
    --yellow: #eab308;
    --cyan: #06b6d4;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    background: var(--bg); color: var(--text); min-height: 100vh;
  }
  .container { max-width: 1200px; margin: 0 auto; padding: 24px; }

  header {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--border);
  }
  header h1 { font-size: 22px; font-weight: 600; }
  header h1 span { color: var(--accent); }
  .nav-link {
    color: var(--accent); text-decoration: none; font-size: 14px;
    padding: 6px 14px; border: 1px solid var(--accent); border-radius: 8px;
    transition: all .15s;
  }
  .nav-link:hover { background: var(--accent); color: #fff; }

  /* ── Filtros ── */
  .match-low { background:#ff4d4d; color:#fff; }
    
  .text-col { font-size: 11px; line-height: 1.3; max-width: 250px; }
  .text-col div { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    
  .filters {
    display:flex; gap:10px; margin-bottom:15px; align-items:center; flex-wrap:wrap;
    padding: 16px; background: var(--card);
    border: 1px solid var(--border); border-radius: 10px;
  }
  .filters label { font-size: 13px; color: var(--muted); }
  .filters select, .filters input {
    background: var(--bg); border: 1px solid var(--border); color: var(--text);
    padding: 8px 12px; border-radius: 6px; font-size: 13px;
  }
  .filters select:focus, .filters input:focus { outline: none; border-color: var(--accent); }
  .btn-filter {
    padding: 8px 18px; border: none; border-radius: 6px; font-size: 13px;
    font-weight: 600; cursor: pointer; background: var(--accent); color: #fff;
    transition: all .15s;
  }
  .btn-filter:hover { background: var(--accent-hover); }

  /* ── Tabela ── */
  .table-wrap {
    background: var(--card); border: 1px solid var(--border); border-radius: 10px;
    overflow: hidden;
  }
  .conf-table { width: 100%; border-collapse: collapse; font-size: 13px; }
  .conf-table th {
    text-align: left; padding: 12px 14px; color: var(--muted); font-weight: 500;
    border-bottom: 1px solid var(--border); font-size: 11px; text-transform: uppercase;
    background: rgba(99,102,241,.04);
  }
  .conf-table td {
    padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,.04);
    vertical-align: middle;
  }
  .conf-table tr:hover { background: rgba(99,102,241,.06); cursor: pointer; }
  .conf-table .num { text-align: right; font-variant-numeric: tabular-nums; }

  /* ── Match badge ── */
  .match-badge {
    display: inline-block; padding: 3px 10px; border-radius: 12px;
    font-size: 12px; font-weight: 600;
  }
  .match-high { background: rgba(34,197,94,.15); color: var(--green); }
  .match-mid { background: rgba(234,179,8,.15); color: var(--yellow); }
  .match-low { background: rgba(239,68,68,.15); color: var(--red); }
  .match-none { background: var(--border); color: var(--muted); }

  /* ── Enunciado truncado ── */
  .enunciado-preview {
    max-width: 350px; overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap; font-size: 12px; color: var(--muted);
  }

  /* ── Paginação ── */
  .pagination {
    display: flex; gap: 8px; justify-content: center; align-items: center;
    margin-top: 20px; padding: 16px;
  }
  .page-btn {
    padding: 6px 14px; border: 1px solid var(--border); border-radius: 6px;
    background: var(--card); color: var(--text); font-size: 13px; cursor: pointer;
    transition: all .15s;
  }
  .page-btn:hover { border-color: var(--accent); color: var(--accent); }
  .page-btn:disabled { opacity: .4; cursor: not-allowed; }
  .page-btn.active { background: var(--accent); border-color: var(--accent); color: #fff; }
  .page-info { color: var(--muted); font-size: 13px; }

  /* ── Modal ── */
  .modal-overlay {
    display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,.7); z-index: 100; justify-content: center;
    align-items: center; backdrop-filter: blur(4px);
  }
  .modal-overlay.active { display: flex; }
  .modal {
    background: var(--card); border: 1px solid var(--border); border-radius: 12px;
    width: 95%; max-width: 1100px; max-height: 90vh; overflow-y: auto;
    padding: 28px; position: relative;
  }
  .modal-close {
    position: absolute; top: 14px; right: 18px; background: none;
    border: none; color: var(--muted); font-size: 22px; cursor: pointer;
    transition: color .15s;
  }
  .modal-close:hover { color: var(--text); }

  .modal h2 { font-size: 18px; margin-bottom: 8px; }
  .modal-meta {
    display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 20px;
    font-size: 13px; color: var(--muted);
  }
  .modal-meta strong { color: var(--text); }

  .compare-grid {
    display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 20px;
  }
  .compare-col {
    background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
    padding: 16px;
  }
  .compare-col h3 {
    font-size: 12px; text-transform: uppercase; color: var(--muted);
    margin-bottom: 10px; letter-spacing: .5px;
  }
  .compare-col .text-content {
    font-size: 13px; line-height: 1.7; white-space: pre-wrap;
    word-break: break-word; max-height: 350px; overflow-y: auto;
  }

  .match-bar-wrap {
    margin-bottom: 20px; padding: 12px 16px;
    background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
  }
  .match-bar-label { font-size: 12px; color: var(--muted); margin-bottom: 6px; }
  .match-bar {
    width: 100%; height: 10px; background: var(--border); border-radius: 5px;
    overflow: hidden;
  }
  .match-bar-fill { height: 100%; border-radius: 5px; transition: width .5s; }
  .match-bar-value {
    font-size: 22px; font-weight: 700; margin-top: 6px;
  }

  .classif-list {
    list-style: none; padding: 0;
  }
  .classif-list li {
    padding: 8px 12px; margin-bottom: 4px; background: var(--bg);
    border: 1px solid var(--border); border-radius: 6px; font-size: 12px;
  }

  .empty-state {
    text-align: center; padding: 60px 20px; color: var(--muted); font-size: 15px;
  }
</style>
</head>
<body>
<div class="container">

  <header>
    <h1>🔎 <span>Conferência</span> de Classificações</h1>
    <a href="/" class="nav-link">← Painel de Controle</a>
  </header>

  <!-- Filtros -->
  <div class="filters">
    <label>Disciplina:</label>
    <select id="filter-disc" onchange="loadData(1)">
      <option value="">Todas</option>
    </select>

    <label>Verificação:</label>
    <select id="filter-verif" onchange="loadData(1)">
      <option value="">Todas</option>
      <option value="true">Pendente ⚠️</option>
      <option value="false">OK ✅</option>
    </select>

    <label>Classificada:</label>
    <select id="filter-classified" onchange="loadData(1)">
      <option value="">Todas</option>
      <option value="true">Sim</option>
      <option value="false">Não</option>
    </select>

    <label>QID:</label>
    <input type="number" id="filter-qid" style="width: 80px;" placeholder="ID">

    <label>SPID:</label>
    <input type="number" id="filter-spid" style="width: 80px;" placeholder="SP ID">

    <label>Início:</label>
    <input type="date" id="filter-date-start">

    <label>Fim:</label>
    <input type="date" id="filter-date-end">

    <label>Itens:</label>
    <select id="filter-perpage" onchange="loadData(1)">
      <option value="10">10</option>
      <option value="20" selected>20</option>
      <option value="50">50</option>
    </select>

    <button class="btn-filter" onclick="loadData(1)">Atualizar</button>

    <span class="page-info" id="results-info"></span>

  </div>

  <!-- Tabela -->
  <div class="table-wrap">
    <table class="conf-table">
      <thead>
        <tr>
          <th>ID</th>
          <th>Disciplina</th>
          <th>Match %</th>
          <th>Simil.</th>
          <th>SP ID</th>
          <th>Status</th>
          <th>Classificação</th>
          <th>Enunciado (nosso)</th>
        </tr>
      </thead>
      <tbody id="conf-body">
        <tr><td colspan="6" class="empty-state">Carregando...</td></tr>
      </tbody>
    </table>
  </div>

  <!-- Paginação -->
  <div class="pagination" id="pagination"></div>

</div>

<!-- Modal de detalhe -->
<div class="modal-overlay" id="modal-overlay">
  <div class="modal">
    <button class="modal-close" onclick="closeModal()">&times;</button>
    <h2 id="modal-title">Questão #—</h2>
    <div class="modal-meta" id="modal-meta"></div>

    <div class="match-bar-wrap">
      <div class="match-bar-label">Taxa de Similaridade</div>
      <div class="match-bar"><div class="match-bar-fill" id="modal-match-bar"></div></div>
      <div class="match-bar-value" id="modal-match-value">—</div>
    </div>

    <div class="compare-grid">
      <div class="compare-col">
        <h3>📝 Nosso Enunciado (tratado)</h3>
        <div class="text-content" id="modal-nosso"></div>
      </div>
      <div class="compare-col">
        <h3>🔗 Enunciado SuperProfessor</h3>
        <div class="text-content" id="modal-superpro"></div>
      </div>
    </div>

    <h3 style="font-size:13px; color:var(--muted); text-transform:uppercase; margin-bottom:8px;">Classificações Extraídas</h3>
    <ul class="classif-list" id="modal-classifs"></ul>
  </div>
</div>

<script>
let currentPage = 1;
let allData = [];

async function init() {
  await loadDisciplinas();
  await loadData(1);
}

async function loadDisciplinas() {
  try {
    const r = await fetch('/api/stats');
    const stats = await r.json();
    const sel = document.getElementById('filter-disc');
    stats.forEach(d => {
      if (d.disciplina_id && d.extraidas > 0) {
        const opt = document.createElement('option');
        opt.value = d.disciplina_id;
        opt.textContent = d.disciplina_nome;
        sel.appendChild(opt);
      }
    });
  } catch(e) { console.error(e); }
}

async function loadData(page) {
  currentPage = page;
  const discId = document.getElementById('filter-disc').value;
  const verif = document.getElementById('filter-verif').value;
  const classified = document.getElementById('filter-classified').value;
  const qid = document.getElementById('filter-qid').value;
  const spid = document.getElementById('filter-spid').value;
  const dateStart = document.getElementById('filter-date-start').value;
  const dateEnd = document.getElementById('filter-date-end').value;
  const perPage = document.getElementById('filter-perpage').value;

  const params = new URLSearchParams({
    page, per_page: perPage, apenas_extraidas: true
  });
  if (discId) params.set('disciplina_id', discId);
  if (verif) params.set('precisa_verificar', verif);
  if (classified) params.set('tem_classificacao', classified);
  if (qid) params.set('questao_id', qid);
  if (spid) params.set('superpro_id', spid);
  if (dateStart) params.set('data_inicio', dateStart);
  if (dateEnd) params.set('data_fim', dateEnd);

  try {
    const r = await fetch(`/api/conferencia?${params}`);
    const data = await r.json();
    allData = data.data || [];
    renderTable(allData);
    renderPagination(data.page, data.pages, data.total);
    document.getElementById('results-info').textContent =
      `${data.total} resultado(s)`;
  } catch(e) {
    console.error(e);
    document.getElementById('conf-body').innerHTML =
      '<tr><td colspan="6" class="empty-state">Erro ao carregar dados</td></tr>';
  }
}

function renderTable(items) {
  const tbody = document.getElementById('conf-body');
  if (!items.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty-state">Nenhuma questão encontrada</td></tr>';
    return;
  }
  tbody.innerHTML = items.map((q, i) => {
    const sim = q.similaridade;
    const pct = sim != null ? (sim * 100).toFixed(1) + '%' : '—';
    const cls = sim == null ? 'match-none'
      : sim >= 0.95 ? 'match-high'
      : sim >= 0.80 ? 'match-mid'
      : 'match-low';
    const enun = (q.enunciado_tratado || q.enunciado_original || '').substring(0, 80);
    
    // Formata classificações como lista HTML
    let classifHtml = '';
    const hasOfficial = q.classificacoes && q.classificacoes.length > 0;
    const hasLowMatch = q.classificacao_nao_enquadrada && q.classificacao_nao_enquadrada.length > 0;
    
    if (hasOfficial) {
      classifHtml = q.classificacoes.map(c => `<div style="font-size:11px; margin-bottom:4px;">${c}</div>`).join('');
    } else if (hasLowMatch) {
      classifHtml = q.classificacao_nao_enquadrada.map(c => `<div style="font-size:11px; margin-bottom:4px; opacity:0.6;">(Low) ${c}</div>`).join('');
    } else {
      classifHtml = '<span style="color:#666">-</span>';
    }

    const verifIcon = q.precisa_verificar ? '⚠️' : '✅';
    const similRaw = q.similaridade ? q.similaridade.toFixed(4) : '-';
    
    return `<tr onclick="openModal(${i})">
      <td>${q.questao_id}</td>
      <td>${q.disciplina_nome || '—'}</td>
      <td><span class="match-badge ${cls}">${pct}</span></td>
      <td class="num">${similRaw}</td>
      <td class="num">${q.superpro_id || '—'}</td>
      <td style="text-align:center">${verifIcon}</td>
      <td class="text-col">${classifHtml}</td>
      <td><div class="enunciado-preview">${escHtml(enun)}</div></td>
    </tr>`;
  }).join('');
}

function renderPagination(page, pages, total) {
  const div = document.getElementById('pagination');
  if (pages <= 1) { div.innerHTML = ''; return; }
  let html = '';
  html += `<button class="page-btn" onclick="loadData(${page-1})" ${page<=1?'disabled':''}>←</button>`;
  const start = Math.max(1, page - 3);
  const end = Math.min(pages, page + 3);
  if (start > 1) html += `<button class="page-btn" onclick="loadData(1)">1</button>`;
  if (start > 2) html += `<span class="page-info">...</span>`;
  for (let i = start; i <= end; i++) {
    html += `<button class="page-btn ${i===page?'active':''}" onclick="loadData(${i})">${i}</button>`;
  }
  if (end < pages - 1) html += `<span class="page-info">...</span>`;
  if (end < pages) html += `<button class="page-btn" onclick="loadData(${pages})">${pages}</button>`;
  html += `<button class="page-btn" onclick="loadData(${page+1})" ${page>=pages?'disabled':''}>→</button>`;
  div.innerHTML = html;
}

function openModal(idx) {
  const q = allData[idx];
  if (!q) return;

  document.getElementById('modal-title').textContent = `Questão #${q.questao_id}`;

  const meta = document.getElementById('modal-meta');
  meta.innerHTML = `
    <span>Disciplina: <strong>${q.disciplina_nome || '—'}</strong></span>
    <span>SuperPro ID: <strong>${q.superpro_id || '—'}</strong></span>
    <span>Verificação: <strong>${q.precisa_verificar ? '⚠️ Pendente' : '✅ OK'}</strong></span>
  `;

  const sim = q.similaridade;
  const pct = sim != null ? (sim * 100).toFixed(1) : 0;
  const bar = document.getElementById('modal-match-bar');
  bar.style.width = (sim != null ? pct : 0) + '%';
  bar.style.background = sim == null ? 'var(--border)'
    : sim >= 0.95 ? 'var(--green)'
    : sim >= 0.80 ? 'var(--yellow)'
    : 'var(--red)';
  const val = document.getElementById('modal-match-value');
  val.textContent = sim != null ? pct + '%' : 'Sem dados';
  val.style.color = sim == null ? 'var(--muted)'
    : sim >= 0.95 ? 'var(--green)'
    : sim >= 0.80 ? 'var(--yellow)'
    : 'var(--red)';

  document.getElementById('modal-nosso').textContent =
    q.enunciado_tratado || q.enunciado_original || '(sem enunciado)';
  document.getElementById('modal-superpro').textContent =
    q.enunciado_superpro || '(sem enunciado do SuperProfessor)';

  const classifUl = document.getElementById('modal-classifs');
  const classifs = q.classificacoes || [];
  classifUl.innerHTML = classifs.length
    ? classifs.map(c => `<li>${escHtml(c)}</li>`).join('')
    : '<li style="color:var(--muted)">Nenhuma classificação</li>';

  document.getElementById('modal-overlay').classList.add('active');
}

function closeModal() {
  document.getElementById('modal-overlay').classList.remove('active');
}

document.getElementById('modal-overlay').addEventListener('click', function(e) {
  if (e.target === this) closeModal();
});

document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') closeModal();
});

function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

init();
</script>
</body>
</html>
"""


# ── HTML da Página de Verificação ────────────────────────────────────

@app.get("/verificacao", response_class=HTMLResponse)
async def verificacao_page():
    return HTML_VERIFICACAO


@app.get("/api/verificacao")
async def get_verificacao(
    page: int = 1,
    per_page: int = 20,
    disciplina_id: int | None = None,
):
    """Proxy para listar questões com precisa_verificar=True."""
    params = {
        "page": page,
        "per_page": per_page,
        "apenas_extraidas": True,
        "precisa_verificar": True,
    }
    if disciplina_id:
        params["disciplina_id"] = disciplina_id

    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.get(f"{API_BASE}/extracao/assuntos", params=params)
        return r.json()


# ── Controle do agente de reclassificação ──

_reclass_state = {
    "running": False,
    "process": None,
    "started_at": None,
    "max_questions": 0,
}
_reclass_logs: deque[str] = deque(maxlen=500)


@app.post("/api/start-reclassificar")
async def start_reclassification(request: Request):
    if _reclass_state["running"]:
        return {"ok": False, "error": "Reclassificação já está rodando"}

    body = await request.json()
    max_q = body.get("max_questions", 0)

    cmd = [
        sys.executable,
        "main.py",
        "--reclassificar",
    ]
    if max_q and max_q > 0:
        cmd.extend(["--max", str(max_q)])

    _reclass_logs.clear()
    _reclass_logs.append(
        f"[{_now()}] Iniciando reclassificação: max={max_q or '∞'}"
    )

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(Path(__file__).parent),
        bufsize=1,
    )

    _reclass_state["running"] = True
    _reclass_state["process"] = proc
    _reclass_state["started_at"] = time.time()
    _reclass_state["max_questions"] = max_q

    asyncio.get_event_loop().run_in_executor(None, _read_reclass_output, proc)

    return {"ok": True, "pid": proc.pid}


@app.post("/api/stop-reclassificar")
async def stop_reclassification():
    if not _reclass_state["running"] or not _reclass_state["process"]:
        return {"ok": False, "error": "Reclassificação não está rodando"}

    proc = _reclass_state["process"]
    proc.terminate()
    _reclass_logs.append(f"[{_now()}] ⏹ Parando reclassificação (PID {proc.pid})...")

    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()

    _reclass_state["running"] = False
    _reclass_state["process"] = None
    _reclass_logs.append(f"[{_now()}] Reclassificação parada.")

    return {"ok": True}


@app.get("/api/reclass-status")
async def get_reclass_status():
    elapsed = ""
    if _reclass_state["started_at"] and _reclass_state["running"]:
        delta = time.time() - _reclass_state["started_at"]
        h, rem = divmod(int(delta), 3600)
        m, s = divmod(rem, 60)
        elapsed = f"{h}h{m:02d}m{s:02d}s"

    return {
        "running": _reclass_state["running"],
        "elapsed": elapsed,
        "max_questions": _reclass_state["max_questions"],
    }


@app.get("/api/reclass-logs")
async def get_reclass_logs(after: int = 0):
    all_logs = list(_reclass_logs)
    return {"logs": all_logs[after:], "total": len(all_logs)}


def _read_reclass_output(proc: subprocess.Popen):
    """Lê stdout do processo de reclassificação."""
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                _reclass_logs.append(line)
                print(line, flush=True)
    except Exception:
        pass
    finally:
        proc.wait()
        msg = f"[{_now()}] Reclassificação encerrada (exit code: {proc.returncode})"
        _reclass_logs.append(msg)
        print(msg, flush=True)
        _reclass_state["running"] = False
        _reclass_state["process"] = None


HTML_VERIFICACAO = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Verificação — Extração SuperProfessor</title>
<style>
  :root {
    --bg: #0f1117;
    --card: #1a1d27;
    --border: #2a2d3a;
    --text: #e4e4e7;
    --muted: #8b8d97;
    --accent: #6366f1;
    --accent-hover: #818cf8;
    --green: #22c55e;
    --red: #ef4444;
    --yellow: #eab308;
    --cyan: #06b6d4;
    --orange: #f97316;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    background: var(--bg); color: var(--text); min-height: 100vh;
  }
  .container { max-width: 1200px; margin: 0 auto; padding: 24px; }

  header {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--border);
  }
  header h1 { font-size: 22px; font-weight: 600; }
  header h1 span { color: var(--orange); }
  .nav-links { display: flex; gap: 10px; align-items: center; }
  .nav-link {
    color: var(--accent); text-decoration: none; font-size: 13px;
    padding: 6px 14px; border: 1px solid var(--accent); border-radius: 8px;
    transition: all .15s;
  }
  .nav-link:hover { background: var(--accent); color: #fff; }

  /* ── Stats ── */
  .stats-bar {
    display: flex; gap: 12px; margin-bottom: 20px; flex-wrap: wrap;
  }
  .stat-box {
    background: var(--card); border: 1px solid var(--border); border-radius: 8px;
    padding: 14px 20px; text-align: center; flex: 1; min-width: 120px;
  }
  .stat-box .val { font-size: 22px; font-weight: 700; }
  .stat-box .lbl { font-size: 11px; color: var(--muted); text-transform: uppercase; margin-top: 4px; }
  .stat-orange .val { color: var(--orange); }
  .stat-green .val { color: var(--green); }

  /* ── Controles ── */
  .controls-bar {
    display: flex; gap: 12px; align-items: center; margin-bottom: 20px;
    flex-wrap: wrap;
    padding: 16px; background: var(--card);
    border: 1px solid var(--border); border-radius: 10px;
  }
  .btn {
    padding: 10px 24px; border: none; border-radius: 8px; font-size: 14px;
    font-weight: 600; cursor: pointer; transition: all .15s;
  }
  .btn-reclass { background: var(--orange); color: #fff; }
  .btn-reclass:hover { background: #ea580c; }
  .btn-reclass:disabled { opacity: .4; cursor: not-allowed; }
  .btn-stop { background: var(--red); color: #fff; }
  .btn-stop:hover { background: #dc2626; }
  .btn-stop:disabled { opacity: .4; cursor: not-allowed; }
  .input-max {
    background: var(--bg); border: 1px solid var(--border); color: var(--text);
    padding: 9px 14px; border-radius: 8px; width: 130px; font-size: 14px;
  }
  .input-max:focus { outline: none; border-color: var(--accent); }
  .input-max::placeholder { color: var(--muted); }
  #reclass-badge {
    padding: 6px 14px; border-radius: 20px; font-size: 13px; font-weight: 500;
  }
  .badge-idle { background: var(--border); color: var(--muted); }
  .badge-running { background: rgba(249,115,22,.15); color: var(--orange); animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:.6; } }
  .elapsed { color: var(--muted); font-size: 13px; }

  /* ── Console ── */
  #reclass-console {
    background: #0a0c10; border: 1px solid var(--border); border-radius: 8px;
    padding: 14px; height: 200px; overflow-y: auto; font-family: 'Cascadia Code', 'Fira Code', monospace;
    font-size: 12px; line-height: 1.7; color: #a1a1aa; margin-bottom: 20px;
  }
  #reclass-console .log-success { color: var(--green); }
  #reclass-console .log-warning { color: var(--yellow); }
  #reclass-console .log-error { color: var(--red); }
  #reclass-console .log-info { color: var(--cyan); }
  #reclass-console .log-match { color: #a78bfa; font-weight: 500; }

  /* ── Tabela ── */
  .table-wrap {
    background: var(--card); border: 1px solid var(--border); border-radius: 10px;
    overflow: hidden;
  }
  .v-table { width: 100%; border-collapse: collapse; font-size: 13px; }
  .v-table th {
    text-align: left; padding: 12px 14px; color: var(--muted); font-weight: 500;
    border-bottom: 1px solid var(--border); font-size: 11px; text-transform: uppercase;
    background: rgba(249,115,22,.04);
  }
  .v-table td {
    padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,.04);
    vertical-align: middle;
  }
  .v-table tr:hover { background: rgba(249,115,22,.06); }
  .v-table .num { text-align: right; font-variant-numeric: tabular-nums; }

  .match-badge {
    display: inline-block; padding: 3px 10px; border-radius: 12px;
    font-size: 12px; font-weight: 600;
  }
  .match-high { background: rgba(34,197,94,.15); color: var(--green); }
  .match-mid { background: rgba(234,179,8,.15); color: var(--yellow); }
  .match-low { background: rgba(239,68,68,.15); color: var(--red); }
  .match-none { background: var(--border); color: var(--muted); }

  .enunciado-preview {
    max-width: 300px; overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap; font-size: 12px; color: var(--muted);
  }

  .classif-list { font-size: 11px; line-height: 1.4; }
  .classif-list div { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 280px; }

  /* ── Paginação ── */
  .pagination {
    display: flex; gap: 8px; justify-content: center; align-items: center;
    margin-top: 20px; padding: 16px;
  }
  .page-btn {
    padding: 6px 14px; border: 1px solid var(--border); border-radius: 6px;
    background: var(--card); color: var(--text); font-size: 13px; cursor: pointer;
  }
  .page-btn:hover { border-color: var(--accent); color: var(--accent); }
  .page-btn:disabled { opacity: .4; cursor: not-allowed; }
  .page-btn.active { background: var(--orange); border-color: var(--orange); color: #fff; }
  .page-info { color: var(--muted); font-size: 13px; }

  #ctrl-msg { margin-top: 8px; font-size: 13px; min-height: 16px; }
</style>
</head>
<body>
<div class="container">

  <header>
    <h1>🔄 <span>Verificação</span> — Reclassificação</h1>
    <div class="nav-links">
      <a class="nav-link" href="/">🤖 Extração</a>
      <a class="nav-link" href="/conferencia">🔎 Conferência</a>
    </div>
  </header>

  <!-- Stats -->
  <div class="stats-bar">
    <div class="stat-box stat-orange"><div class="val" id="st-total">—</div><div class="lbl">Pendentes</div></div>
    <div class="stat-box stat-green"><div class="val" id="st-done">—</div><div class="lbl">Total Questões</div></div>
  </div>

  <!-- Controles de reclassificação -->
  <div class="controls-bar">
    <label style="font-size:13px; color:var(--muted);">Limite:</label>
    <input type="number" class="input-max" id="max-reclass" value="0" min="0" placeholder="0=todos">
    <button class="btn btn-reclass" id="btn-reclass" onclick="startReclass()">🔄 Iniciar Reclassificação</button>
    <button class="btn btn-stop" id="btn-stop-reclass" onclick="stopReclass()" disabled>⏹ Parar</button>
    <span class="elapsed" id="reclass-elapsed"></span>
    <span id="reclass-badge" class="badge-idle">Parado</span>
    <div id="ctrl-msg"></div>
  </div>

  <!-- Console de logs -->
  <div id="reclass-console"><span style="color:var(--muted)">Aguardando...</span></div>

  <!-- Tabela de questões precisa_verificar -->
  <div class="table-wrap">
    <table class="v-table">
      <thead>
        <tr>
          <th>ID</th>
          <th>Questão</th>
          <th>Disciplina</th>
          <th>SP ID</th>
          <th>Match</th>
          <th>Enunciado</th>
          <th>Classificações</th>
        </tr>
      </thead>
      <tbody id="verificacao-body"><tr><td colspan="7" style="text-align:center; color:var(--muted); padding:30px;">Carregando...</td></tr></tbody>
    </table>
  </div>

  <div class="pagination" id="pagination"></div>
</div>

<script>
let vPage = 1;
let vTotal = 0;
let reclassRunning = false;
let reclassPolling = null;
let reclassLogIndex = 0;

async function init() {
  await loadVerificacao();
  checkReclassStatus();
  setInterval(checkReclassStatus, 3000);
}

// ── Carregar questões ──
async function loadVerificacao(page = 1) {
  vPage = page;
  try {
    const r = await fetch(`/api/verificacao?page=${page}&per_page=20`);
    const data = await r.json();
    renderTable(data.items || []);
    renderPagination(data.page || 1, data.pages || 1, data.total || 0);
    document.getElementById('st-total').textContent = fmt(data.total || 0);
    document.getElementById('st-done').textContent = fmt((data.total || 0));
  } catch(e) {
    console.error('Erro:', e);
    document.getElementById('verificacao-body').innerHTML =
      '<tr><td colspan="7" style="text-align:center; color:var(--red); padding:30px;">Erro ao carregar</td></tr>';
  }
}

function renderTable(items) {
  const tbody = document.getElementById('verificacao-body');
  if (!items.length) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; color:var(--green); padding:30px;">✅ Nenhuma questão pendente de verificação!</td></tr>';
    return;
  }
  tbody.innerHTML = items.map(q => {
    const sim = q.similaridade;
    let matchCls = 'match-none', matchTxt = '—';
    if (sim != null) {
      const pct = (sim * 100).toFixed(0);
      matchTxt = pct + '%';
      matchCls = sim >= 0.9 ? 'match-high' : sim >= 0.8 ? 'match-mid' : 'match-low';
    }
    const classifs = (q.classificacoes || []).slice(0, 3);
    const classifsHtml = classifs.map(c => `<div title="${escHtml(c)}">${escHtml(c)}</div>`).join('');
    const enunciado = (q.enunciado_tratado || '').substring(0, 80);

    return `<tr>
      <td class="num">${q.questao_id}</td>
      <td class="num">${q.questao_id_str || ''}</td>
      <td>${q.disciplina_nome || '—'}</td>
      <td class="num">${q.superpro_id || '—'}</td>
      <td><span class="match-badge ${matchCls}">${matchTxt}</span></td>
      <td><div class="enunciado-preview" title="${escHtml(q.enunciado_tratado || '')}">${escHtml(enunciado)}</div></td>
      <td><div class="classif-list">${classifsHtml || '<span style="color:var(--muted)">—</span>'}</div></td>
    </tr>`;
  }).join('');
}

function renderPagination(current, total, count) {
  const div = document.getElementById('pagination');
  if (total <= 1) { div.innerHTML = ''; return; }
  let html = `<button class="page-btn" onclick="loadVerificacao(${current-1})" ${current<=1?'disabled':''}>← Anterior</button>`;
  html += `<span class="page-info">Página ${current} de ${total} (${fmt(count)} questões)</span>`;
  html += `<button class="page-btn" onclick="loadVerificacao(${current+1})" ${current>=total?'disabled':''}>Próxima →</button>`;
  div.innerHTML = html;
}

// ── Controle reclassificação ──
async function startReclass() {
  const maxQ = +document.getElementById('max-reclass').value || 0;
  document.getElementById('btn-reclass').disabled = true;
  showMsg('Iniciando reclassificação...', 'var(--yellow)');

  try {
    const r = await fetch('/api/start-reclassificar', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ max_questions: maxQ })
    });
    const data = await r.json();
    if (data.ok) {
      showMsg(`Reclassificação iniciada (PID ${data.pid})`, 'var(--green)');
      setReclassRunning(true);
      reclassLogIndex = 0;
      document.getElementById('reclass-console').innerHTML = '';
      startReclassPolling();
    } else {
      showMsg(data.error, 'var(--red)');
      document.getElementById('btn-reclass').disabled = false;
    }
  } catch(e) {
    showMsg('Erro ao conectar', 'var(--red)');
    document.getElementById('btn-reclass').disabled = false;
  }
}

async function stopReclass() {
  document.getElementById('btn-stop-reclass').disabled = true;
  showMsg('Parando...', 'var(--yellow)');
  try {
    const r = await fetch('/api/stop-reclassificar', { method: 'POST' });
    const data = await r.json();
    showMsg(data.ok ? 'Reclassificação parada' : data.error, data.ok ? 'var(--green)' : 'var(--red)');
  } catch(e) { showMsg('Erro', 'var(--red)'); }
}

async function checkReclassStatus() {
  try {
    const r = await fetch('/api/reclass-status');
    const data = await r.json();
    const badge = document.getElementById('reclass-badge');
    const elapsed = document.getElementById('reclass-elapsed');

    if (data.running) {
      badge.textContent = 'Rodando';
      badge.className = 'badge-running';
      elapsed.textContent = data.elapsed;
      if (!reclassRunning) { setReclassRunning(true); startReclassPolling(); }
    } else {
      badge.textContent = 'Parado';
      badge.className = 'badge-idle';
      elapsed.textContent = '';
      if (reclassRunning) {
        setReclassRunning(false);
        loadVerificacao(vPage); // Refresh table
      }
    }
  } catch(e) {}
}

function setReclassRunning(v) {
  reclassRunning = v;
  document.getElementById('btn-reclass').disabled = v;
  document.getElementById('btn-stop-reclass').disabled = !v;
  if (!v && reclassPolling) { clearInterval(reclassPolling); reclassPolling = null; }
}

function startReclassPolling() {
  if (reclassPolling) clearInterval(reclassPolling);
  reclassPolling = setInterval(pollReclassLogs, 800);
}

async function pollReclassLogs() {
  try {
    const r = await fetch(`/api/reclass-logs?after=${reclassLogIndex}`);
    const data = await r.json();
    if (data.logs.length) {
      const con = document.getElementById('reclass-console');
      data.logs.forEach(line => {
        const div = document.createElement('div');
        div.className = getLogClass(line);
        div.textContent = line;
        con.appendChild(div);
      });
      con.scrollTop = con.scrollHeight;
      reclassLogIndex = data.total;
    }
  } catch(e) {}
}

function getLogClass(line) {
  if (line.includes('RECLASS') || line.includes('✅') || line.includes('MATCH')) return 'log-match';
  if (line.includes('WARNING') || line.includes('⚠')) return 'log-warning';
  if (line.includes('ERROR') || line.includes('❌')) return 'log-error';
  if (line.includes('INFO')) return 'log-info';
  return '';
}

// ── Helpers ──
function fmt(n) { return n != null ? n.toLocaleString('pt-BR') : '—'; }
function showMsg(msg, color) {
  const el = document.getElementById('ctrl-msg');
  el.textContent = msg;
  el.style.color = color || 'var(--text)';
}
function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

init();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn

    print(f"🌐 Interface disponível em: http://localhost:8501")
    print(f"📡 API backend: {API_BASE}")
    uvicorn.run(app, host="0.0.0.0", port=8501, log_level="info")

