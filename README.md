# Survey Insight Copilot — Web版（公開デモ）

学生の「知りたいこと」から、根拠付きのアンケート分析と次に聞くべき設問までをつなぐ調査支援ツール。
このディレクトリは GitHub Pages で配信する**サーバー不要版**です。

## AI対話は停止しています

**公開版ではAI対話（チャット）を無効にしています。** 分析・設問生成・仮説提案・レポートは
そのまま使えます。

理由：静的サイトに置いたファイルは誰でもダウンロードできるので、APIキーを埋め込むと
開発者ツールを開いた第三者があなたのキーで課金できてしまいます。難読化しても防げません。

**有効化する手順は [`py/llm_adapter.py`](py/llm_adapter.py) の冒頭コメントに全部書いてあります。**
要点は「APIキーをブラウザではなく中継サーバー（Cloudflare Workers 等）に置き、
`PROXY_ENDPOINT` を設定して `complete()` のコメントを外す」の3手順です。
`app.js` 先頭の `CHAT_ENABLED` も `true` にしてください。

公開URLで有効化する場合は、**中継サーバーに必ずレート制限を入れてください**。
入れないとURLを知った誰かが呼び続けてトークン代が青天井になります。

## しくみ：Python をブラウザの中で動かしている

分析ロジックを JavaScript に書き直すのではなく、[Pyodide](https://pyodide.org)（WebAssembly版CPython）で
**ローカル版と同じ Python ファイルをそのまま実行**しています。書き換えないので、
ローカル版と結果がずれる余地がありません。

```
ブラウザ
├── app.js …………… 画面（ローカル版と同じ。api() の呼び先だけ差し替え）
├── js/bridge.js …… Pyodide を起動し、api() を Python へ橋渡し
└── Pyodide（CPython + pandas）
    ├── py/router.py ……… server.py のディスパッチ相当
    └── py/*.py ………… ローカル版と同一の分析モジュール群
```

| | ローカル版 | Web版（このディレクトリ） |
|---|---|---|
| 実行 | `python server.py`（port 8766） | 静的配信のみ |
| 分析 | CPython + pandas | Pyodide 上の同じコード |
| 保存 | `runtime/survey-insight.db` | ブラウザのメモリ（タブを閉じると消える） |
| AI対話 | Claude CLI / APIキーで有効 | **停止**（上記参照） |
| 認証 | アクセスコード | なし（守るサーバーが無いため） |
| 熱海サンプル | `runtime/uploads` の実データ | **なし**（実データを公開しないため） |

## データの扱い

- 読み込んだ回答は Pyodide の仮想ファイルシステム（**メモリ上**）だけに置かれ、外部送信しません。
- プロジェクト・セッションはブラウザのメモリ上の SQLite。**タブを閉じると消えます**。
- 同梱の `sample-data/atami-student-sample.csv` は乱数シード固定で生成した**合成データ115件**で、
  実際の回答ではありません。実データ（`runtime/uploads/`・`survey-insight.db`）は含めていません。

## 制限

- 初回だけ Pyodide と pandas のダウンロードに10〜30秒かかります（以降はブラウザキャッシュ）。
- `/api/atami-dr3/saved`（保存済みアンケートの固定分析）は実データ同梱が前提のため使えません。
  手元のファイルを選ぶ `analyze` を使ってください。
- Pyodide は CDN（jsdelivr）から読み込みます。オフラインでは起動しません。

## ローカルでの確認

```bash
python -m http.server 7797 --directory tools/survey-insight-web
```
