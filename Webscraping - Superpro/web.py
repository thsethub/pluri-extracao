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
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{API_BASE}/extracao/stats", params={"ano_id": 3})
        return r.json()


@app.delete("/api/reset")
async def reset_db():
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.delete(f"{API_BASE}/extracao/reset")
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


if __name__ == "__main__":
    import uvicorn

    print(f"🌐 Interface disponível em: http://localhost:8501")
    print(f"📡 API backend: {API_BASE}")
    uvicorn.run(app, host="0.0.0.0", port=8501, log_level="info")
