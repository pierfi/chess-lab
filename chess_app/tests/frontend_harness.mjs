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
    // WebSocket inerte: jsdom non lo implementa. Nessun evento automatico
    // (nessun onclose → nessuna riconnessione), così l'harness può invocare a
    // mano gameSocket.onmessage per esercitare il dedup del client.
    window.WebSocket = class {
      constructor(url){ this.url = url; this.readyState = 1; }
      send(){}
      close(){ this.readyState = 3; }
    };
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
check('time control: partita non a tempo -> clock nascosti', ev('state.timeControl') === null && $('clock-top').style.display !== 'flex');

// ---- Apertura ECO: wiring end-to-end (la correttezza del dataset/matching è
// già coperta da pytest — qui si verifica solo che il backend esponga il
// campo e che il frontend lo mostri/nasconda correttamente) ----
const stateResp = await (await fetch(`${API}/game/${ev('state.gameId')}`)).json();
check('opening: campo presente nella risposta reale del backend', 'opening' in stateResp, JSON.stringify(stateResp.opening));
ev("state.opening = { eco: 'B20', name: 'Sicilian Defense' }; renderOpening();");
check('opening: display mostra eco e nome',
  $('opening-display').textContent.includes('B20') && $('opening-display').textContent.includes('Sicilian Defense'));
check('opening: display visibile quando valorizzato', $('opening-display').classList.contains('visible'));
ev('state.opening = null; renderOpening();');
check('opening: display nascosto quando null',
  !$('opening-display').classList.contains('visible') && $('opening-display').textContent === '');

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

// ---- Time control (Fase 6): preset selector, digital clock, bandierina ----
ev('openSetup()');
check('time control: 7 preset nel selettore (incl. "Nessun limite")',
  window.document.querySelectorAll('#time-row button').length === 7);
const bulletBtn = [...window.document.querySelectorAll('#time-row button')]
  .find(b => b.textContent.includes('Bullet 1+0'));
bulletBtn.click();  // click DOM reale, non ev() diretto sullo state
check('time control: click preset aggiorna setup.time', ev('setup.time') === 'bullet1');
ev("closeSetup(); state.playerColor = 'white'; state.engineElo = 400;");
await ev('startGame()');  // bypassa requestGameStart/confirm modal, stesso pattern del resto dell'harness
check('time control: nuova partita a tempo creata', !!ev('state.gameId'));
check('time control: state.timeControl = 1+0 (60s, incremento 0)',
  ev('state.timeControl.initial_seconds') === 60 && ev('state.timeControl.increment_seconds') === 0);
check('time control: clock iniziali a 60000ms per lato',
  ev('state.clock.white') === 60000 && ev('state.clock.black') === 60000);
check('time control: box clock visibili (partita a tempo)',
  $('clock-top').style.display === 'flex' && $('clock-bottom').style.display === 'flex');
check('time control: clock del player (bottom, bianco) attivo — tocca a lui',
  $('clock-bottom').classList.contains('active'));
check('time control: clock avversario (top) non attivo',
  !$('clock-top').classList.contains('active'));

// Countdown previsionale client-side: dopo >1s deve essere sceso sotto il valore iniziale,
// riconciliato solo alla prossima risposta server (nessun polling in questa fase, per design).
await sleep(1100);
const bottomSecs = $('clock-bottom-time').textContent;
check('time control: countdown client-side sceso sotto 1:00',
  bottomSecs !== '01:00' && bottomSecs !== '60:00', bottomSecs);

// Una mossa reale riconcilia il clock col valore autoritativo del server.
await ev("sendMove('e2e4')");
check('time control: clock riconciliato dal server dopo la mossa (< iniziale)',
  ev('state.clock.white') < 60000 && ev('state.clock.white') > 0, ev('state.clock.white'));

// Bandierina: la UI di game-over (banner + testo + classi clock) è verificata
// iniettando uno stato sintetico via updateState() — stesso pattern già usato
// più sotto per "in presa" (window.__presa) — senza dover davvero aspettare
// il timeout reale lato server (coperto invece dai test pytest dedicati).
window.__timeoutPayload = {
  fen: ev('state.fen'),
  turn: 'white',
  is_game_over: true,
  is_check: false,
  move_history: JSON.parse(ev('JSON.stringify(state.moveHistory)')),
  move_history_san: JSON.parse(ev('JSON.stringify(state.moveHistorySan)')),
  pgn: ev('state.pgn'),
  result: '0-1',
  time_control: { initial_seconds: 60, increment_seconds: 0 },
  clock: { white: 0, black: 45000 },
  game_over: { result: '0-1', reason: 'timeout' },
};
ev('updateState(window.__timeoutPayload)');
check('bandierina: banner mostra "Tempo scaduto"',
  $('game-over-banner').textContent.includes('Tempo scaduto'), $('game-over-banner').textContent);
check('bandierina: clock del flaggato (bianco, bottom) sotto soglia bassa',
  $('clock-bottom').classList.contains('low'));
check('bandierina: clock avversario (nero, top) non in soglia bassa',
  !$('clock-top').classList.contains('low'));
check('bandierina: nessun clock resta "active" a partita finita',
  !$('clock-top').classList.contains('active') && !$('clock-bottom').classList.contains('active'));

// Nuova partita SENZA time control: i clock tornano nascosti (resetPlayUi pulisce lo stato).
ev("state.playerColor = 'white'; state.engineElo = 400; setup.time = 'none';");
await ev('startGame()');
check('time control: nuova partita "Nessun limite" nasconde di nuovo i clock',
  ev('state.timeControl') === null && $('clock-top').style.display !== 'flex');

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

// ---- WebSocket dedup: un game-over deve bypassare il dedup ply-based ----
// Regressione (intersezione time-control × websocket): con la bandierina la
// mossa che fa scattare il flag non viene mai applicata, quindi il ply non
// avanza. Una tab non-attiva riceve la notifica ma il dedup basato sul ply la
// scarterebbe SE non fosse per il segnale is_game_over. Verifica il vero
// handler onmessage: soppresso a ply invariato senza game-over, forzato con.
ev("state.playerColor='white'; state.engineElo=400; setup.time='none';");
await ev('startGame()');
check('ws-dedup: socket mock connesso dopo startGame', ev('!!gameSocket'),
  ev('gameSocket && gameSocket.constructor && gameSocket.constructor.name'));
const wsGid = ev('state.gameId');
ev('state.thinking = false; state.isGameOver = false;');
const wsBasePly = ev('state.moveHistory.length');
// Spia fetch: conta solo i GET di stato /game/<id> (esclude hint/threats che
// updateState innesca a valle, irrilevanti per il dedup).
ev(`window.__wsFetches = []; window.__realFetch = window.fetch;
    window.fetch = (u, ...r) => {
      const s = String(u);
      if (s.includes('/game/${wsGid}') && !s.includes('/hint') && !s.includes('/threats')) window.__wsFetches.push(s);
      return window.__realFetch(u, ...r);
    };`);
// Caso 1: notifica NON game-over con ply invariato → soppressa (nessun refetch).
ev(`gameSocket.onmessage({ data: JSON.stringify({ type:'state', game_id:'${wsGid}', ply:${wsBasePly}, is_game_over:false }) })`);
await sleep(250);
check('ws-dedup: notifica non-over a ply invariato viene soppressa',
  ev('window.__wsFetches.length') === 0, ev('JSON.stringify(window.__wsFetches)'));
// Caso 2: notifica game-over con ply invariato → NON soppressa (refetch forzato).
ev(`gameSocket.onmessage({ data: JSON.stringify({ type:'state', game_id:'${wsGid}', ply:${wsBasePly}, is_game_over:true }) })`);
await waitFor(() => ev('window.__wsFetches.length') >= 1, 4000);
check('ws-dedup: notifica game-over a ply invariato forza il refetch',
  ev('window.__wsFetches.length') >= 1, ev('JSON.stringify(window.__wsFetches)'));
ev('window.fetch = window.__realFetch;');

const fails = results.filter(r => !r.ok).length;
console.log('\n' + (results.length - fails) + '/' + results.length + ' checks ok');
process.exit(fails ? 1 : 0);
