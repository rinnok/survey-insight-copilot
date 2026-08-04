"""llm_adapter の公開版スタブ —— AI対話機能は無効化してあります。

========================================================================
 ■ この機能を有効にするには（公開URLでは既定で無効）
========================================================================

公開URL（GitHub Pages）はブラウザに配るだけの静的サイトです。ここに置いた
ファイルは誰でもダウンロードできるので、**APIキーを書き込むと、開発者ツールを
開いた第三者があなたのキーで課金できてしまいます**。難読化しても防げません。
そのため公開版では対話機能を止め、このスタブに差し替えています。

有効化するには、次の 3 手順が必要です。

 1) 中継サーバー（プロキシ）を用意する
    APIキーはブラウザではなくサーバー側に置きます。中継するのは
    「システムプロンプト＋ユーザープロンプト → Claude の応答テキスト」だけです。
    Cloudflare Workers なら無料枠（10万リクエスト/日）で足ります。

    Worker 側の実装イメージ（TypeScript）:

        export default {
          async fetch(request: Request, env: { ANTHROPIC_API_KEY: string }) {
            const { system, prompt } = await request.json();
            const res = await fetch("https://api.anthropic.com/v1/messages", {
              method: "POST",
              headers: {
                "x-api-key": env.ANTHROPIC_API_KEY,   // ← 鍵はここ（サーバー側）
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
              },
              body: JSON.stringify({
                model: "claude-haiku-4-5",
                max_tokens: 800,
                system,
                messages: [{ role: "user", content: prompt }],
              }),
            });
            return new Response(await res.text(), {
              headers: { "content-type": "application/json" },
            });
          },
        };

    APIキーは `wrangler secret put ANTHROPIC_API_KEY` で登録します
    （コードにも wrangler.toml にも書かないこと）。

 2) 下の PROXY_ENDPOINT に、その Worker の URL を設定する
    例: PROXY_ENDPOINT = "https://survey-llm.<your-subdomain>.workers.dev"

 3) 下の `complete()` の中の「■ 有効化するときはここのコメントを外す」
    ブロックのコメントを外し、その上の `raise LLMError(...)` を消す

 ※ 公開URLに置く場合は、Worker 側に必ずレート制限（IP単位で1日N回など）を
    入れてください。入れないと、URL を知った誰かが呼び続けてトークン代が
    無制限に膨らみます。用途もこのアプリの対話に限定してください
    （任意のプロンプトを通す汎用プロキシにすると踏み台になります）。

========================================================================

ローカル版（tools/survey-insight-copilot/llm_adapter.py）は Claude CLI・
ANTHROPIC_API_KEY・OPENAI_API_KEY を自動判別して呼び分けます。そちらは
サーバーが手元にあるので鍵が漏れず、この制約はありません。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# 手順 2) で自分の中継サーバーURLを入れる。空文字のままなら無効。
PROXY_ENDPOINT = ""

# 対話が無効なときに画面へ出す理由。status() 経由でUIに表示される。
DISABLED_REASON = "public_build"
DISABLED_MESSAGE = (
    "公開版ではAI対話を無効にしています（APIキーを静的サイトに置けないため）。"
    "分析・設問生成・レポートはそのまま使えます。"
)


class LLMError(RuntimeError):
    """LLM呼び出しの失敗。research_dialogue はこれを捕まえてルールベースに落ちる。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


def status() -> dict[str, Any]:
    """UIに出す接続状態。公開版は常に無効。"""
    if not PROXY_ENDPOINT:
        return {
            "enabled": False,
            "provider": "disabled",
            "model": "",
            "reason": DISABLED_REASON,
            "message": DISABLED_MESSAGE,
        }
    return {
        "enabled": True,
        "provider": "proxy",
        "model": "claude-haiku-4-5",
        "reason": "",
        "message": "",
    }


def is_enabled() -> bool:
    return bool(PROXY_ENDPOINT)


def complete(system_prompt: str, user_prompt: str) -> LLMResult:
    """LLMを呼ぶ。公開版では必ず LLMError を投げ、呼び出し側がルールベースへ落ちる。"""
    raise LLMError(DISABLED_REASON, DISABLED_MESSAGE)

    # ■ 有効化するときはここのコメントを外す（上の raise は消す）
    # 手順 1) の中継サーバーを立て、手順 2) で PROXY_ENDPOINT を設定してから。
    #
    # from pyodide.http import pyfetch  # ブラウザからのHTTPはこれを使う
    #
    # response = await pyfetch(          # ※ complete を async def に変え、
    #     PROXY_ENDPOINT,                #    呼び出し側も await にする必要がある
    #     method="POST",
    #     headers={"Content-Type": "application/json"},
    #     body=json.dumps({"system": system_prompt, "prompt": user_prompt}),
    # )
    # if response.status != 200:
    #     raise LLMError("proxy_error", f"中継サーバーがエラーを返しました（{response.status}）")
    # payload = await response.json()
    # blocks = payload.get("content") or []
    # text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    # if not text:
    #     raise LLMError("empty_response", "応答が空でした")
    # usage = payload.get("usage") or {}
    # return LLMResult(
    #     text=text,
    #     provider="proxy",
    #     model=payload.get("model", ""),
    #     input_tokens=int(usage.get("input_tokens", 0)),
    #     output_tokens=int(usage.get("output_tokens", 0)),
    # )
