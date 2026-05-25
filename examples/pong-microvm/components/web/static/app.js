const canvas = document.getElementById("game");
const ctx = canvas.getContext("2d");
const scoreEl = document.getElementById("score");
const triesEl = document.getElementById("tries");
const p1NameEl = document.getElementById("p1Name");
const p2NameEl = document.getElementById("p2Name");
const mmukoStateEl = document.getElementById("mmukoState");
const polycallStateEl = document.getElementById("polycallState");
const connectionLineEl = document.getElementById("connectionLine");
const playersEl = document.getElementById("players");
const eventLogEl = document.getElementById("eventLog");
const targetScoreEl = document.getElementById("targetScore");
const practiceBtn = document.getElementById("practiceBtn");
const scanBtn = document.getElementById("scanBtn");
const voiceBtn = document.getElementById("voiceBtn");

const assets = {
  board: loadImage("/assets/Board.png"),
  player: loadImage("/assets/Player.png"),
  computer: loadImage("/assets/Computer.png"),
  ball: loadImage("/assets/Ball.png"),
  score: loadImage("/assets/ScoreBar.png")
};

const state = {
  width: 1280,
  height: 720,
  targetScore: 5,
  mode: "practice",
  tries: 3,
  p1: 0,
  p2: 0,
  matchId: "local-practice",
  aiTargetY: 360,
  lastAgentCall: 0,
  paddle: {
    w: 24,
    h: 112,
    p1y: 304,
    p2y: 304,
    speed: 600
  },
  ball: {
    x: 640,
    y: 360,
    r: 12,
    vx: 420,
    vy: 240
  },
  pointerY: null,
  keys: new Set(),
  lastFrame: performance.now()
};

function loadImage(src) {
  const image = new Image();
  image.src = src;
  return image;
}

function resize() {
  const dpr = Math.max(1, Math.min(window.devicePixelRatio || 1, 2));
  canvas.width = Math.floor(window.innerWidth * dpr);
  canvas.height = Math.floor(window.innerHeight * dpr);
  canvas.style.width = `${window.innerWidth}px`;
  canvas.style.height = `${window.innerHeight}px`;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  state.width = window.innerWidth;
  state.height = window.innerHeight;
  state.paddle.h = Math.max(84, Math.min(138, state.height * 0.16));
  clampPaddles();
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function clampPaddles() {
  const half = state.paddle.h / 2;
  state.paddle.p1y = clamp(state.paddle.p1y, half, state.height - half);
  state.paddle.p2y = clamp(state.paddle.p2y, half, state.height - half);
}

function resetBall(direction = 1) {
  state.ball.x = state.width / 2;
  state.ball.y = state.height / 2;
  state.ball.vx = direction * (340 + Math.random() * 150);
  state.ball.vy = (Math.random() > 0.5 ? 1 : -1) * (170 + Math.random() * 140);
}

function resetMatch() {
  state.p1 = 0;
  state.p2 = 0;
  state.tries = 3;
  resetBall(Math.random() > 0.5 ? 1 : -1);
  updateScore();
}

function updateScore() {
  scoreEl.textContent = `${state.p1} : ${state.p2}`;
  triesEl.textContent = String(state.tries);
}

function updateBadge(element, label) {
  element.textContent = label;
  element.className = "";
  element.classList.add(label);
}

function addLog(title, detail = "") {
  const row = document.createElement("div");
  row.innerHTML = `<span>${new Date().toLocaleTimeString()}</span>${escapeHtml(title)}${detail ? ` - ${escapeHtml(detail)}` : ""}`;
  eventLogEl.prepend(row);
  while (eventLogEl.children.length > 8) {
    eventLogEl.removeChild(eventLogEl.lastChild);
  }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#039;"
  }[char]));
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {})
    }
  });
  return response.json();
}

async function refreshMmuko() {
  try {
    const result = await api("/api/mmuko/status");
    const label = result.dominant || "NOSIGNAL";
    updateBadge(mmukoStateEl, label);
    connectionLineEl.textContent = `${label} ${result.fingerprint || ""}`;
  } catch (error) {
    updateBadge(mmukoStateEl, "NOSIGNAL");
  }
}

async function scanPlayers() {
  scanBtn.disabled = true;
  scanBtn.textContent = "Scanning";
  try {
    const result = await api("/api/scan", { method: "POST", body: "{}" });
    renderPlayers(result.players || []);
    addLog("scan complete", `${(result.players || []).length} player(s)`);
  } catch (error) {
    addLog("scan failed", error.message);
  } finally {
    scanBtn.disabled = false;
    scanBtn.textContent = "Scan";
  }
}

function renderPlayers(players) {
  playersEl.textContent = "";
  if (!players.length) {
    const empty = document.createElement("div");
    empty.className = "player-card";
    empty.textContent = "No players discovered";
    playersEl.appendChild(empty);
    return;
  }
  for (const player of players) {
    const card = document.createElement("article");
    card.className = "player-card";
    const label = player.state || "NOSIGNAL";
    card.innerHTML = `
      <header>
        <strong>${escapeHtml(player.name || "unknown player")}</strong>
        <span class="badge ${label}">${label}</span>
      </header>
      <span>${escapeHtml(player.address || "127.0.0.1")}:${escapeHtml(player.port || "")}</span>
      <button type="button">Invite</button>
    `;
    card.querySelector("button").addEventListener("click", () => invitePlayer(player));
    playersEl.appendChild(card);
  }
}

async function invitePlayer(player) {
  state.targetScore = Number(targetScoreEl.value);
  const invite = await api("/api/invite", {
    method: "POST",
    body: JSON.stringify({ targetId: player.id, points: state.targetScore })
  });
  if (!invite.ok) {
    addLog("invite failed", invite.error || "unknown");
    return;
  }
  const accepted = await api("/api/accept", {
    method: "POST",
    body: JSON.stringify({ inviteId: invite.inviteId })
  });
  if (accepted.ok) {
    state.mode = "relay";
    state.matchId = accepted.matchId;
    p1NameEl.textContent = "player 1";
    p2NameEl.textContent = "player 2";
    resetMatch();
    addLog("match connected", `${accepted.points} points`);
  }
}

async function negotiateVoice() {
  try {
    const result = await api("/api/voice/negotiate", {
      method: "POST",
      body: JSON.stringify({ matchId: state.matchId, players: [p1NameEl.textContent, p2NameEl.textContent] })
    });
    if (result.ok) {
      addLog("voice channel", `${result.mode} ${result.state}`);
    }
  } catch (error) {
    addLog("voice failed", error.message);
  }
}

function startPolycallStream() {
  try {
    const stream = new EventSource("/api/polycall/stream");
    stream.onmessage = (event) => {
      const data = JSON.parse(event.data);
      polycallStateEl.textContent = data.adapterFallback ? "adapter" : "native";
      if (data.event) {
        addLog(data.event, data.state || data.reason || "");
      }
    };
    stream.onerror = () => {
      polycallStateEl.textContent = "closed";
      stream.close();
    };
  } catch (error) {
    polycallStateEl.textContent = "offline";
  }
}

function startGameEvents() {
  try {
    const stream = new EventSource("/api/game/events");
    stream.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.kind === "heartbeat") {
        connectionLineEl.textContent = `${data.state} players=${data.players}`;
      }
    };
    stream.onerror = () => stream.close();
  } catch (error) {
    addLog("events offline", error.message);
  }
}

async function askUAgent() {
  const now = performance.now();
  if (now - state.lastAgentCall < 300) {
    return;
  }
  state.lastAgentCall = now;
  try {
    const result = await api("/api/u/predict", {
      method: "POST",
      body: JSON.stringify({
        agent: p2NameEl.textContent,
        ball: state.ball,
        canvas: { width: state.width, height: state.height },
        paddleHeight: state.paddle.h
      })
    });
    if (result.ok) {
      state.aiTargetY = Number(result.targetY);
    }
  } catch (error) {
    state.aiTargetY = state.ball.y;
  }
}

function update(dt) {
  const paddle = state.paddle;
  if (state.pointerY !== null) {
    paddle.p1y += (state.pointerY - paddle.p1y) * Math.min(1, dt * 12);
  }
  if (state.keys.has("ArrowUp") || state.keys.has("w")) {
    paddle.p1y -= paddle.speed * dt;
  }
  if (state.keys.has("ArrowDown") || state.keys.has("s")) {
    paddle.p1y += paddle.speed * dt;
  }

  askUAgent();
  paddle.p2y += (state.aiTargetY - paddle.p2y) * Math.min(1, dt * 5.5);
  clampPaddles();

  const ball = state.ball;
  ball.x += ball.vx * dt;
  ball.y += ball.vy * dt;

  if (ball.y < ball.r || ball.y > state.height - ball.r) {
    ball.y = clamp(ball.y, ball.r, state.height - ball.r);
    ball.vy *= -1;
  }

  const leftX = 54;
  const rightX = state.width - 54;
  if (ball.vx < 0 && hitPaddle(leftX, paddle.p1y)) {
    ball.x = leftX + paddle.w + ball.r;
    ball.vx = Math.abs(ball.vx) * 1.035;
    ball.vy += (ball.y - paddle.p1y) * 4;
    sendGameInput("p1-hit");
  }
  if (ball.vx > 0 && hitPaddle(rightX, paddle.p2y)) {
    ball.x = rightX - paddle.w - ball.r;
    ball.vx = -Math.abs(ball.vx) * 1.035;
    ball.vy += (ball.y - paddle.p2y) * 4;
    sendGameInput("p2-hit");
  }

  if (ball.x < -40) {
    state.p2 += 1;
    scorePoint(-1);
  }
  if (ball.x > state.width + 40) {
    state.p1 += 1;
    scorePoint(1);
  }
}

function hitPaddle(x, y) {
  const ball = state.ball;
  const paddle = state.paddle;
  return (
    ball.x - ball.r < x + paddle.w &&
    ball.x + ball.r > x &&
    ball.y + ball.r > y - paddle.h / 2 &&
    ball.y - ball.r < y + paddle.h / 2
  );
}

function scorePoint(direction) {
  state.tries = Math.max(0, state.tries - 1);
  if (state.p1 >= state.targetScore || state.p2 >= state.targetScore) {
    addLog("match complete", state.p1 > state.p2 ? "player 1 wins" : `${p2NameEl.textContent} wins`);
    resetMatch();
    return;
  }
  updateScore();
  resetBall(direction);
  sendGameInput("score");
}

let lastInputSent = 0;
function sendGameInput(kind) {
  const now = performance.now();
  if (now - lastInputSent < 450 && kind !== "score") {
    return;
  }
  lastInputSent = now;
  api("/api/game/input", {
    method: "POST",
    body: JSON.stringify({
      matchId: state.matchId,
      kind,
      score: [state.p1, state.p2],
      ball: state.ball,
      mode: state.mode
    })
  }).catch(() => {});
}

function draw() {
  ctx.clearRect(0, 0, state.width, state.height);
  if (assets.board.complete && assets.board.naturalWidth) {
    ctx.drawImage(assets.board, 0, 0, state.width, state.height);
  } else {
    ctx.fillStyle = "#101714";
    ctx.fillRect(0, 0, state.width, state.height);
  }

  ctx.strokeStyle = "rgba(243,246,238,0.28)";
  ctx.lineWidth = 2;
  ctx.setLineDash([14, 14]);
  ctx.beginPath();
  ctx.moveTo(state.width / 2, 92);
  ctx.lineTo(state.width / 2, state.height);
  ctx.stroke();
  ctx.setLineDash([]);

  drawPaddle(54, state.paddle.p1y, assets.player, "#a9d271");
  drawPaddle(state.width - 54 - state.paddle.w, state.paddle.p2y, assets.computer, "#9bbcff");
  drawBall();
}

function drawPaddle(x, centerY, image, fallback) {
  const w = state.paddle.w;
  const h = state.paddle.h;
  const y = centerY - h / 2;
  if (image.complete && image.naturalWidth) {
    ctx.drawImage(image, x - 10, y, w + 20, h);
  } else {
    ctx.fillStyle = fallback;
    ctx.fillRect(x, y, w, h);
  }
}

function drawBall() {
  const ball = state.ball;
  if (assets.ball.complete && assets.ball.naturalWidth) {
    ctx.drawImage(assets.ball, ball.x - ball.r, ball.y - ball.r, ball.r * 2, ball.r * 2);
  } else {
    ctx.fillStyle = "#fff3b0";
    ctx.beginPath();
    ctx.arc(ball.x, ball.y, ball.r, 0, Math.PI * 2);
    ctx.fill();
  }
}

function frame(now) {
  const dt = Math.min(0.032, (now - state.lastFrame) / 1000);
  state.lastFrame = now;
  update(dt);
  draw();
  requestAnimationFrame(frame);
}

window.addEventListener("resize", resize);
window.addEventListener("pointermove", (event) => {
  state.pointerY = event.clientY;
});
window.addEventListener("keydown", (event) => state.keys.add(event.key));
window.addEventListener("keyup", (event) => state.keys.delete(event.key));

targetScoreEl.addEventListener("change", () => {
  state.targetScore = Number(targetScoreEl.value);
  resetMatch();
});

practiceBtn.addEventListener("click", () => {
  state.mode = "practice";
  state.matchId = "local-practice";
  p1NameEl.textContent = "player 1u";
  p2NameEl.textContent = "player 2u";
  resetMatch();
  addLog("practice mode", "u enabled");
});

scanBtn.addEventListener("click", scanPlayers);
voiceBtn.addEventListener("click", negotiateVoice);

resize();
resetMatch();
refreshMmuko();
scanPlayers();
startPolycallStream();
startGameEvents();
setInterval(refreshMmuko, 3500);
requestAnimationFrame(frame);
