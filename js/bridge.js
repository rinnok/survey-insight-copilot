// ブラウザ内で Python を動かし、app.js の api() から呼べるようにする層。
//
// ローカル版は Python サーバー（server.py）が /api/* を処理していた。公開版は
// Pyodide（WebAssembly版のCPython）を読み込み、同じ Python コードを
// ブラウザの中で動かす。分析ロジック（analysis_engine.py ほか）はローカル版と
// 同じファイルをそのまま使うので、結果がずれる余地がない。
//
// 回答データは Pyodide の仮想ファイルシステム（メモリ上）にしか置かれず、
// ネットワークには出ない。タブを閉じれば消える。

const PYODIDE_VERSION = "0.29.2";
const PYODIDE_BASE = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;

// py/ 配下の Python を仮想ファイルシステムへ書き込む。router が import する順。
const PY_MODULES = [
  "llm_adapter.py",
  "analysis_engine.py",
  "branching_survey.py",
  "declared_survey.py",
  "evidence_engine.py",
  "atami_dr3.py",
  "questionnaire.py",
  "questionnaire_import.py",
  "research_dialogue.py",
  "project_store.py",
  "session_store.py",
  "router.py",
];

// branching_survey.load_snapshot が読む調査票スナップショット
const PY_DATA = ["documentation/fixtures/atami-form-snapshot.v1.json"];

let pyodide = null;
let bootPromise = null;

function setBootStatus(text, detail = "") {
  const el = document.getElementById("bootStatus");
  if (el) el.textContent = detail ? `${text}（${detail}）` : text;
}

async function loadScriptOnce(src) {
  await new Promise((resolve, reject) => {
    const tag = document.createElement("script");
    tag.src = src;
    tag.onload = resolve;
    tag.onerror = () => reject(new Error("Pyodide を読み込めませんでした（通信環境を確認してください）"));
    document.head.append(tag);
  });
}

async function boot() {
  setBootStatus("Python実行環境を準備しています", "初回は少し時間がかかります");
  await loadScriptOnce(`${PYODIDE_BASE}pyodide.js`);
  pyodide = await globalThis.loadPyodide({ indexURL: PYODIDE_BASE });

  // sqlite3 は Pyodide では標準ライブラリから切り出されているので明示的に読む
  // （project_store / session_store の永続化に使う）
  setBootStatus("分析ライブラリを読み込んでいます", "pandas / sqlite3");
  await pyodide.loadPackage(["pandas", "sqlite3", "micropip"]);

  // openpyxl（Excel読み込み）・pypdf・python-docx（設問票の取り込み）は
  // Pyodide 同梱パッケージに無いので micropip で PyPI から入れる。
  setBootStatus("Excel / 文書パーサを読み込んでいます", "openpyxl / pypdf / python-docx");
  const micropip = pyodide.pyimport("micropip");
  await micropip.install(["openpyxl", "pypdf", "python-docx"]);

  setBootStatus("アプリのコードを読み込んでいます");
  const root = "/home/pyodide/app";
  pyodide.FS.mkdirTree(`${root}/documentation/fixtures`);
  pyodide.FS.mkdirTree(`${root}/runtime/uploads`); // ProjectStore が起動時に作る想定の場所

  const fetchText = async (name) => {
    const response = await fetch(new URL(`../py/${name}`, import.meta.url), { cache: "no-store" });
    if (!response.ok) throw new Error(`${name} を読み込めませんでした`);
    return response.text();
  };
  await Promise.all([...PY_MODULES, ...PY_DATA].map(async (name) => {
    pyodide.FS.writeFile(`${root}/${name}`, await fetchText(name));
  }));

  setBootStatus("初期化しています");
  pyodide.runPython(`
import sys
sys.path.insert(0, "${root}")
import router
`);
  setBootStatus("");
  return pyodide;
}

export function ready() {
  if (!bootPromise) bootPromise = boot();
  return bootPromise;
}

// app.js の api() から呼ばれる。fetch と同じ形（status + data）を返す。
export async function request(method, path, body) {
  await ready();
  const bodyJson = body === undefined ? "" : JSON.stringify(body);
  const handle = pyodide.globals.get("router").handle_json;
  const raw = handle(method, path, bodyJson);
  const payload = JSON.parse(raw);
  handle.destroy?.();
  return payload;
}
