// Transcription page for Harness Maxxing: paste a link (YouTube or other) or send an audio / video file, get the full
// text with "Copy". The work runs in the background on the server (transcribe.py); the page polls every 3 s while a
// job runs. History: the last 30.
const CHUNK = 3 * 1024 * 1024; // 3 MB per chunk -> 4 MB in base64, under the 5 MB limit of an extension request
const ENGINE_KEY = "transcription.engine";

const TEXTS = {
  en: {
    title: "Transcription",
    intro: "A YouTube video, another link or an audio / video file → the full text, word for word.",
    url: "Paste the address (YouTube, podcast…)",
    go: "Transcribe", send: "Send a file", copy: "Copy", copied: "Copied", show: "Show", hide: "Hide",
    remove: "Delete", chars: "characters", empty: "No transcription yet.",
    done: "ready", error: "failed", running: "running", sending: "Sending", locale: "en-GB",
    engine: "Engine", auto: "Automatic", harness: "Harness voice engine", gemini: "Gemini",
    noKey: "YouTube links need a Gemini key: Settings → Keys → GEMINI_API_KEY. Files use the voice engine of Settings → Voice.",
    notStarted: "Transcription not started: ", sendFailed: "Upload failed: ", listFailed: "List unreadable: ",
    copyFailed: "Copy failed: ",
  },
  fr: {
    title: "Transcription",
    intro: "Une vidéo YouTube, un autre lien ou un fichier audio / vidéo → le texte intégral, mot pour mot.",
    url: "Coller l'adresse (YouTube, podcast…)",
    go: "Transcrire", send: "Envoyer un fichier", copy: "Copier", copied: "Copié", show: "Afficher", hide: "Masquer",
    remove: "Supprimer", chars: "caractères", empty: "Aucune transcription pour l'instant.",
    done: "prêt", error: "échec", running: "en cours", sending: "Envoi", locale: "fr-BE",
    engine: "Moteur", auto: "Automatique", harness: "Moteur vocal de Harness", gemini: "Gemini",
    noKey: "Les liens YouTube demandent une clé Gemini : Réglages → Clés → GEMINI_API_KEY. Les fichiers passent par le moteur vocal de Réglages → Voix.",
    notStarted: "Transcription non lancée : ", sendFailed: "Envoi échoué : ", listFailed: "Liste illisible : ",
    copyFailed: "Copie impossible : ",
  },
};

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function btn(ctx, label, icon, cls) {
  const b = el("button", "ui-btn ui-sm " + (cls || ""), label);
  if (icon) b.prepend(ctx.icon(icon));
  return b;
}

function b64(buf) {
  const bytes = new Uint8Array(buf);
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

const msg = (e) => (e && e.message ? e.message : String(e));

async function copy(text, area) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    area.select(); // fallback: select + old-style copy
    document.execCommand("copy");
  }
}

function jobCard(ctx, T, j, open, actions) {
  const card = el("div", "ui-card");
  card.style.cssText = "padding:.75rem;gap:.5rem;display:flex;flex-direction:column";
  const head = el("div", "ui-row");
  head.style.cssText = "gap:.5rem;flex-wrap:wrap";
  const tone = { done: "ui-success", error: "ui-error", running: "ui-primary" }[j.status];
  head.append(el("span", "ui-badge " + tone, T[j.status]));
  const label = el("span", "ui-text", j.label);
  label.style.cssText = "flex:1;min-width:12rem;overflow-wrap:anywhere";
  const when = new Intl.DateTimeFormat(T.locale, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  head.append(label, el("span", "ui-muted ui-small", when.format(new Date(j.started * 1000))));
  card.append(head);
  if (j.status === "running") {
    card.append(el("p", "ui-muted ui-small", j.step));
    return card;
  }
  if (j.status === "error") card.append(el("div", "ui-callout ui-error", j.error));
  const bar = el("div", "ui-row");
  bar.style.gap = ".5rem";
  const area = el("textarea", "ui-textarea");
  area.readOnly = true;
  area.rows = 14;
  area.style.cssText = "width:100%;font-size:.9rem;line-height:1.45";
  if (j.status === "done") {
    const cp = btn(ctx, T.copy, "copy", "ui-primary");
    const show = btn(ctx, open ? T.hide : T.show, "file-text", "ui-outline");
    bar.append(cp, show, el("span", "ui-muted ui-small", `${j.chars.toLocaleString(T.locale)} ${T.chars}`));
    const fill = async () => {
      if (!area.value) area.value = (await actions.text(j.id)).text;
    };
    const reset = () => { cp.disabled = false; cp.replaceChildren(ctx.icon("copy"), document.createTextNode(" " + T.copy)); };
    cp.onclick = async () => {
      cp.disabled = true;
      try {
        await fill();
        await copy(area.value, area);
        cp.replaceChildren(ctx.icon("check"), document.createTextNode(" " + T.copied));
      } catch (e) {
        actions.fail(T.copyFailed + msg(e));
      } finally {
        setTimeout(reset, 1500);
      }
    };
    show.onclick = () => actions.toggle(j.id);
    if (open) fill().catch((e) => actions.fail(msg(e)));
  }
  const del = btn(ctx, "", "trash", "ui-ghost ui-icon-only");
  del.title = T.remove;
  del.style.marginLeft = "auto";
  del.onclick = () => actions.remove(j.id);
  bar.append(del);
  card.append(bar);
  if (open && j.status === "done") card.append(area);
  return card;
}

function engineSelect(T) {
  const wrap = el("label", "ui-row ui-small ui-muted", T.engine + " ");
  wrap.style.cssText = "display:flex;align-items:center;gap:.5rem";
  const sel = el("select", "ui-select ui-sm");
  sel.style.cssText = "width:auto;min-width:14rem";
  for (const id of ["auto", "harness", "gemini"]) {
    const o = el("option", null, T[id]);
    o.value = id;
    sel.append(o);
  }
  sel.value = localStorage.getItem(ENGINE_KEY) || "auto";
  sel.onchange = () => localStorage.setItem(ENGINE_KEY, sel.value);
  wrap.append(sel);
  return [wrap, sel];
}

function buildForm(ctx, T) {
  const form = el("form", "ui-row");
  form.style.cssText = "gap:.5rem;flex-wrap:wrap";
  const url = el("input", "ui-input");
  url.placeholder = T.url;
  url.style.cssText = "flex:1;min-width:16rem";
  const go = el("button", "ui-btn", T.go);
  go.type = "submit";
  go.prepend(ctx.icon("mic"));
  const pick = el("input");
  pick.type = "file";
  pick.accept = "audio/*,video/*";
  pick.style.display = "none";
  const send = btn(ctx, T.send, "upload", "ui-outline");
  send.type = "button";
  form.append(url, go, send, pick);
  return { form, url, go, pick, send };
}

// Sends the file in chunks; the last answer is the started job.
async function uploadFile(ctx, T, f, engine, bar, isGone) {
  let upload = null;
  let got = null;
  const n = Math.max(1, Math.ceil(f.size / CHUNK));
  for (let i = 0; i < n && !isGone(); i++) {
    const data = b64(await f.slice(i * CHUNK, (i + 1) * CHUNK).arrayBuffer());
    got = await ctx.api("upload", { method: "POST",
      body: { upload, name: f.name, index: i, offset: i * CHUNK, data, last: i === n - 1, engine } });
    if (got.error) throw new Error(got.error);
    upload = got.upload;
    bar.textContent = `${T.sending} ${f.name} : ${Math.round(((i + 1) / n) * 100)} %`;
  }
  return got;
}

// The list of jobs: draws the cards, reloads every 3 s while a job runs. -> { load, stop }
function jobList(ctx, T, list, hint, fail) {
  let jobs = [];
  let openId = null;
  let timer = null;
  let gone = false;
  const actions = {
    text: async (id) => {
      const got = await ctx.api("jobs/" + id);
      if (got.error) throw new Error(got.error);
      return got;
    },
    toggle: (id) => { openId = openId === id ? null : id; draw(); },
    remove: async (id) => {
      const got = await ctx.api("jobs/" + id, { method: "DELETE" });
      if (got.error) fail(got.error);
      await load();
    },
    fail,
  };
  function draw() {
    if (!jobs.length) {
      list.replaceChildren(el("p", "ui-empty", T.empty));
      return;
    }
    list.replaceChildren(...jobs.map((j) => jobCard(ctx, T, j, j.id === openId, actions)));
  }
  async function load() {
    try {
      const got = await ctx.api("jobs");
      if (got.error) throw new Error(got.error);
      const before = new Set(jobs.filter((j) => j.status === "running").map((j) => j.id));
      jobs = got.jobs;
      hint.replaceChildren(...(got.gemini ? [] : [el("div", "ui-callout ui-info", T.noKey)]));
      const finished = jobs.find((j) => before.has(j.id) && j.status === "done");
      if (finished) openId = finished.id; // a job just finished: its text opens
      draw();
    } catch (e) {
      fail(T.listFailed + msg(e));
    }
    clearTimeout(timer);
    if (!gone && jobs.some((j) => j.status === "running")) timer = setTimeout(load, 3000);
  }
  return { load, stop: () => { gone = true; clearTimeout(timer); } };
}

export function mount(root, ctx) {
  const T = TEXTS[ctx.lang] || TEXTS.en;
  const page = el("div", "ui-page");
  page.style.maxWidth = "960px";
  const { form, url, go, pick, send } = buildForm(ctx, T);
  const [engineRow, engine] = engineSelect(T);
  const hint = el("div");
  const notice = el("div");
  const list = el("div", "ui-stack");
  page.append(el("h1", "ui-h1", T.title), el("p", "ui-muted", T.intro), form, engineRow, hint, notice, list);
  root.replaceChildren(page);

  const fail = (text) => notice.replaceChildren(el("div", "ui-callout ui-error", text));
  const jl = jobList(ctx, T, list, hint, fail);
  const load = jl.load;
  let gone = false;
  async function started(got) {
    if (got.error) throw new Error(got.error);
    notice.replaceChildren();
    await load();
  }
  form.onsubmit = async (ev) => {
    ev.preventDefault();
    if (!url.value.trim()) return;
    go.disabled = true;
    try {
      await started(await ctx.api("jobs", { method: "POST", body: { url: url.value.trim(), engine: engine.value } }));
      url.value = "";
    } catch (e) {
      fail(T.notStarted + msg(e));
    } finally {
      go.disabled = false;
    }
  };
  send.onclick = () => pick.click();
  pick.onchange = async () => {
    const f = pick.files[0];
    pick.value = "";
    if (!f) return;
    send.disabled = go.disabled = true;
    const bar = el("div", "ui-callout ui-info", T.sending + "…");
    notice.replaceChildren(bar);
    try {
      const got = await uploadFile(ctx, T, f, engine.value, bar, () => gone);
      await started(got);
    } catch (e) {
      fail(T.sendFailed + msg(e));
    } finally {
      send.disabled = go.disabled = false;
    }
  };
  load();
  return () => { gone = true; jl.stop(); };
}
