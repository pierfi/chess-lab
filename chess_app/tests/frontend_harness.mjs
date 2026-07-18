// Harness jsdom per la verifica del frontend senza browser (nessun Chromium
// disponibile in sandbox): carica il VERO index.html, patcha solo la porta
// API, stubba AudioContext/scrollIntoView (assenti in jsdom, presenti in ogni
// browser reale) e guida i flussi reali delle viste con fetch() veri contro un
// backend live isolato (DB scratch). Lanciare con tests/run_frontend_harness.sh,
// che avvia il backend e installa jsdom in tests/.harness_deps (fuori da git:
// il frontend dell'app resta a zero dipendenze npm).
//
// Riusato a mano nelle Fasi 3/4/5 e mai committato (gap segnalato in
// docs/status.md): questa è la versione salvata nel repo.
import { createRequire } from 'node:module';
import fs from 'node:fs';

const require = createRequire(new URL('./.harness_deps/', import.meta.url));
const { JSDOM } = require('jsdom');

const PORT = process.env.HARNESS_PORT || '8977';
const API = `http://localhost:${PORT}`;
const HTML_PATH = new URL('../frontend/index.html', import.meta.url);
let html = fs.readFileSync(HTML_PATH, 'utf8');
html = html.replace("const API = 'http://localhost:8765'", `const API = '${API}'`);

const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  url: 'http://localhost/',
  pretendToBeVisual: true,
  resources: undefined, // niente caricamento <img> (asset SVG irrilevanti qui)
  beforeParse(window) {
    window.AudioContext = class {
      createOscillator(){ return { connect(){}, start(){}, stop(){}, frequency:{ value:0, linearRampToValueAtTime(){} } }; }
      createGain(){ return { connect(){}, gain:{ value:0, exponentialRampToValueAtTime(){} } }; }
      get destination(){ return {}; } get currentTime(){ return 0; }
    };
    window.Element.prototype.scrollIntoView = () => {};
    window.fetch = (...a) => fetch(...a); // fetch reale di Node contro il backend live
  },
});
const { window } = dom;
const ev = code => window.eval(code);

const sleep = ms => new Promise(r => setTimeout(r, ms));
const $ = id => window.document.getElementById(id);
const results = [];
function check(name, cond, extra='') {
  results.push({ name, ok: !!cond });
  console.log((cond ? 'PASS' : 'FAIL') + '  ' + name + (extra ? '  [' + extra + ']' : ''));
}
async function waitFor(fn, ms=40000, step=150) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { if (fn()) return true; await sleep(step); }
  return false;
}

// ---- Vista Gioca: nuova partita + una mossa ----
ev("state.playerColor='white'; state.engineElo=400;");
await ev('startGame()');
check('startGame crea partita', !!ev('state.gameId'), ev('state.gameId'));
check('board renderizzata (64 caselle)', window.document.querySelectorAll('#board-wrapper .square').length === 64);
await ev("sendMove('e2e4')");
check('sendMove aggiorna history (player+engine)', ev('state.moveHistory.length') === 2, ev('state.moveHistory.join(",")'));
check('move list mostra SAN', $('move-list').textContent.includes('e4'));
check('assisted off: toggle pezzi in presa nascosto', $('threat-toggle').style.display !== 'block');

// ---- Assisted mode: hint + eval bar + frecce + sotto-toggle in presa ----
ev('toggleAssisted()');
const gotHint = await waitFor(() => ev('state.hint !== null'), 60000);
check('assisted: hint arrivato', gotHint, gotHint ? 'eval_cp=' + ev('state.hint.eval_cp') : 'timeout');
check('assisted: frecce SVG presenti', !!window.document.querySelector('#board-wrapper .arrow-layer'));
check('assisted: pannello hint popolato', $('hint-panel').textContent.trim().length > 0);
check('assisted: selettore forza visibile', $('hint-strength').style.display === 'block');
check('assisted: toggle pezzi in presa visibile', $('threat-toggle').style.display === 'block');
check('assisted: pezzi in presa ON per default', $('threat-checkbox').checked === true);
ev('toggleAssisted()');
check('assisted off: toggle pezzi in presa nascosto di nuovo', $('threat-toggle').style.display !== 'block');

// ---- Analisi post-partita ----
await ev('requestAnalysis()');
check('analisi: sezione visibile', $('analysis').classList.contains('visible'));
check('analisi: eval chart SVG', $('eval-chart').innerHTML.includes('<svg'));
check('analisi: tabella due colonne', window.document.querySelectorAll('.analysis-row').length > 0);

// ---- Pezzi in presa: posizione deterministica via start_fen ----
// Cavallo bianco d4 attaccato dal pedone e5 e indifeso -> unico pezzo in presa.
const PRESA_FEN = 'k7/8/8/4p3/3N4/8/8/K7 w - - 0 1';
const presaResp = await fetch(API + '/game/new', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ player_color: 'white', engine_elo: 400, start_fen: PRESA_FEN }),
});
window.__presa = await presaResp.json();
check('in presa: partita da FEN custom creata', !!window.__presa.game_id, window.__presa.game_id);

// Verifica diretta dell'endpoint (shape della risposta)
const thrData = await (await fetch(`${API}/game/${window.__presa.game_id}/threats`)).json();
check('in presa: endpoint /threats side=white', thrData.side === 'white');
check('in presa: endpoint /threats rileva Nd4 (attackers e5, value 3)',
  thrData.in_presa.length === 1 && thrData.in_presa[0].square === 'd4'
  && thrData.in_presa[0].piece === 'N' && thrData.in_presa[0].value === 3
  && thrData.in_presa[0].attackers.length === 1 && thrData.in_presa[0].attackers[0] === 'e5',
  JSON.stringify(thrData.in_presa));

// Ora nel frontend: assistita ON + updateState -> refetch automatico di /threats
ev('toggleAssisted()');
ev("state.gameId = window.__presa.game_id; state.playerColor = 'white';");
ev('updateState(window.__presa)');
const gotGlow = await waitFor(() => window.document.querySelectorAll('#board-wrapper .square.in-presa').length > 0, 20000);
check('in presa: glow .in-presa comparso dopo updateState', gotGlow);
const glowSquares = [...window.document.querySelectorAll('#board-wrapper .square.in-presa')].map(d => d.dataset.sq);
check('in presa: glow solo su d4', glowSquares.length === 1 && glowSquares[0] === 'd4', glowSquares.join(','));
check('in presa: nessun king-check (lo scacco resta un linguaggio distinto)',
  window.document.querySelectorAll('#board-wrapper .square.king-check').length === 0);

// Sotto-toggle OFF via evento change reale -> glow spento, senza uscire dall'assistita
$('threat-checkbox').checked = false;
$('threat-checkbox').dispatchEvent(new window.Event('change'));
check('in presa: sotto-toggle OFF spegne il glow',
  window.document.querySelectorAll('#board-wrapper .square.in-presa').length === 0);
check('in presa: assistita resta attiva col sotto-toggle OFF', ev('state.assisted') === true);

// Sotto-toggle di nuovo ON -> refetch e glow di nuovo acceso
$('threat-checkbox').checked = true;
$('threat-checkbox').dispatchEvent(new window.Event('change'));
const glowBack = await waitFor(() => window.document.querySelectorAll('#board-wrapper .square.in-presa').length === 1, 20000);
check('in presa: sotto-toggle ON riaccende il glow', glowBack);

// Uscita dall'assistita -> tutto spento e nascosto
ev('toggleAssisted()');
check('in presa: assistita OFF spegne glow e toggle',
  window.document.querySelectorAll('#board-wrapper .square.in-presa').length === 0
  && $('threat-toggle').style.display !== 'block');

// ---- Vista Storico ----
ev("showView('history')");
await waitFor(() => $('history-list').querySelectorAll('.game-row').length > 0, 15000);
const rows = $('history-list').querySelectorAll('.game-row').length;
check('storico: righe partite', rows > 0, rows + ' righe');

// replay della prima partita con mosse
const histData = await (await fetch(`${API}/games?per_page=10`)).json();
const item = histData.items.find(i => i.move_count > 0);
window.__item = item;
await ev('openReplay(window.__item)');
check('replay: sezione aperta', $('replay-section').style.display === 'block');
ev('replayStep(1); replayStep(1)');
check('replay: navigazione avanti', ev('replayGame.idx') === 2);
check('replay: board renderizzata', window.document.querySelectorAll('#replay-board .square').length === 64);
ev('closeReplay()');

// import PGN dalla textarea
$('import-pgn').value = '[Event "t"]\n\n1. d4 d5 2. c4 e6 *';
await ev('importPgn()');
await waitFor(() => $('import-msg').textContent.includes('importata'), 10000);
check('import PGN: messaggio ok', $('import-msg').textContent.includes('importata'), $('import-msg').textContent);
check('import PGN: filtro passa a Importate', $('hist-source').value === 'import');

// ---- Vista Crescita ----
ev("showView('growth')");
await waitFor(() => $('growth-cards').querySelectorAll('.stat-box').length > 0, 15000);
check('crescita: 6 stat card', $('growth-cards').querySelectorAll('.stat-box').length === 6);
check('crescita: grafico ELO presente', $('elo-chart').innerHTML.length > 0);
check('crescita: grafico accuracy presente', $('acc-chart').innerHTML.length > 0);

// ---- Vista Allenamento ----
ev("showView('training')");
await waitFor(() => !ev('training.loading') && (ev('!!training.puzzle') || ev('!!training.emptyMsg')), 20000);
check('allenamento: puzzle o coda vuota gestita', ev('!!training.puzzle') || ev('!!training.emptyMsg'),
  ev('training.puzzle ? "puzzle "+training.puzzle.puzzle_id : training.emptyMsg'));
if (ev('!!training.puzzle')) {
  check('allenamento: board puzzle', window.document.querySelectorAll('#puzzle-board .square').length === 64);
  await ev("submitPuzzleAnswer('a2a3')");
  check('allenamento: feedback risposta', ev('training.answered') === true, $('puzzle-feedback').textContent);
}
await waitFor(() => $('endgame-list').querySelectorAll('.eg-row').length > 0, 15000);
check('allenamento: lista drill (16)', $('endgame-list').querySelectorAll('.eg-row').length === 16);
await waitFor(() => $('weakness-content').textContent.length > 0, 15000);
check('allenamento: dashboard debolezze', $('weakness-content').querySelectorAll('.train-bar-row').length > 0 || $('weakness-content').textContent.includes('Nessuna'));

// ---- Vista Puzzle (Fase 6 — dataset Lichess esterno) ----
// La soluzione non è mai esposta dall'API: il harness la legge dal bundle
// statico versionato (stessa fonte con cui il backend semina external_puzzles).
const bundle = JSON.parse(fs.readFileSync(new URL('../backend/data/lichess_puzzles.json', import.meta.url), 'utf8'));
const solOf = id => bundle.find(p => p.id === id).moves;
ev("showView('puzzles')");
await waitFor(() => !ev('ext.loading') && ev('!!ext.puzzle'), 15000);
check('puzzle: caricato dal bundle', ev('!!ext.puzzle'), ev('ext.puzzle && ext.puzzle.puzzle_id'));
check('puzzle: board renderizzata (64 caselle)', window.document.querySelectorAll('#ext-board .square').length === 64);
check('puzzle: meta mostra il rating', $('ext-meta').textContent.includes(String(ev('ext.puzzle.rating'))));
await waitFor(() => $('ext-theme').options.length > 1, 10000);
check('puzzle: select temi popolata', $('ext-theme').options.length > 1, $('ext-theme').options.length + ' opzioni');

// happy path: risolvi l'intera linea con la soluzione vera
let solvedId = ev('ext.puzzle.puzzle_id');
{
  const sol = solOf(solvedId);
  for (let i = 0; i < sol.length && !ev('ext.finished'); i += 2) {
    await ev(`submitExtAnswer('${sol[i]}')`);
  }
}
check('puzzle: risolto con la soluzione', ev('ext.finished') && !ev('ext.failed'));
check('puzzle: feedback ok', $('ext-feedback').classList.contains('ok'), $('ext-feedback').textContent);
check('puzzle: punteggio sessione 1/1', ev('ext.solved') === 1 && ev('ext.attempted') === 1);

// prossimo puzzle: exclude evita la ripetizione immediata
await ev('loadExtPuzzle()');
await waitFor(() => !ev('ext.loading') && ev('!!ext.puzzle'), 15000);
check('puzzle: exclude evita ripetizione', ev('ext.puzzle.puzzle_id') !== solvedId, ev('ext.puzzle.puzzle_id'));

// fail path: prova mosse candidate diverse dall'attesa finché una legale
// chiude il puzzle (le illegali vengono respinte con 400 e non lo terminano)
{
  const expected = solOf(ev('ext.puzzle.puzzle_id'))[0];
  const cands = JSON.parse(ev(`JSON.stringify((() => {
    const map = fenToMap(ext.fen); const out = [];
    for (const sq of Object.keys(map)) {
      const p = map[sq];
      const own = ext.puzzle.player_to_move === 'white' ? p === p.toUpperCase() : p === p.toLowerCase();
      if (own) for (const m of generateMoveCandidates(ext.fen, sq, ext.puzzle.player_to_move))
        out.push(m.from + m.to + (m.promo ? 'q' : ''));
    }
    return out;
  })())`));
  for (const uci of cands) {
    if (uci === expected) continue;
    await ev(`submitExtAnswer('${uci}')`);
    if (ev('ext.finished')) break;
  }
  if (ev('ext.altMate')) {
    console.log('SKIP  puzzle: fail path (matto alternativo trovato per caso)');
  } else {
    check('puzzle: mossa sbagliata -> fallito', ev('ext.finished') && ev('ext.failed'));
    check('puzzle: feedback ko con mossa attesa', $('ext-feedback').classList.contains('ko')
      && $('ext-feedback').textContent.includes(expected.slice(0, 2)), $('ext-feedback').textContent);
    check('puzzle: punteggio sessione 1/2', ev('ext.solved') === 1 && ev('ext.attempted') === 2);
  }
}
// drill di finali: avvio dalla vista Allenamento -> ruota nella vista Gioca
ev("showView('training')");
await waitFor(() => ev('endgames.length') > 0, 5000);
await ev("startEndgameDrill(endgames.find(d => d.id === 'philidor'))");
check('drill: partita creata con FEN custom', ev('state.fen').startsWith('8/8/8/3k4'), ev('state.fen'));
check('drill: player = lato al tratto (nero)', ev('state.playerColor') === 'black');
check('drill: vista Gioca attiva', $('view-play').style.display === 'flex');
check('drill: status mostra obiettivo', $('status').textContent.includes('Philidor'), $('status').textContent);

const fails = results.filter(r => !r.ok).length;
console.log('\n' + (results.length - fails) + '/' + results.length + ' checks ok');
process.exit(fails ? 1 : 0);
