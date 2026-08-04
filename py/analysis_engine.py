"""Survey Insight Copilot のローカル分析エンジン。

自然言語から分析計画を提案する部分と、実データを計算する部分を分離する。
個票はメモリ上だけで扱い、APIへ返すのは列概要と匿名集計値のみ。
"""
from __future__ import annotations

import hashlib
import io
import math
import re
from pathlib import Path
from typing import Any

import pandas as pd

MULTI_SEP = re.compile(r"[;；\n]|,\s*")
MAX_VALUES = 12


def clean(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value).strip()


def read_table(filename: str, raw: bytes) -> pd.DataFrame:
    suffix = Path(filename).suffix.lower()
    stream = io.BytesIO(raw)
    if suffix in {".xlsx", ".xlsm"}:
        try:
            return pd.read_excel(stream)
        except Exception as exc:
            raise ValueError(f"Excelを読み込めませんでした: {exc}") from exc
    if suffix == ".csv":
        for encoding in ("utf-8-sig", "cp932", "utf-8"):
            try:
                stream.seek(0)
                return pd.read_csv(stream, encoding=encoding)
            except UnicodeDecodeError:
                continue
            except Exception as exc:
                raise ValueError(f"CSVを読み込めませんでした: {exc}") from exc
        raise ValueError("CSVの文字コードを判定できませんでした")
    raise ValueError("対応形式は .xlsx / .xlsm / .csv です")


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _tokens(series: pd.Series) -> pd.Series:
    return (
        series.map(clean)
        .loc[lambda s: s != ""]
        .map(lambda value: [part.strip() for part in MULTI_SEP.split(value) if part.strip()])
        .explode()
        .dropna()
    )


def inspect_dataset(df: pd.DataFrame) -> dict[str, Any]:
    columns = []
    for col in df.columns:
        series = df[col].map(clean)
        answered = series[series != ""]
        values = answered.value_counts().head(MAX_VALUES)
        tokens = _tokens(series).value_counts().head(MAX_VALUES)
        numeric = pd.to_numeric(answered.str.replace(",", "", regex=False), errors="coerce")
        numeric_share = float(numeric.notna().mean()) if len(answered) else 0.0
        columns.append({
            "name": str(col),
            "shortName": short_label(str(col)),
            "nonEmpty": int(len(answered)),
            "missing": int(len(df) - len(answered)),
            "unique": int(answered.nunique()),
            "kind": "numeric" if numeric_share >= 0.8 and answered.nunique() > 5 else "categorical",
            "looksMultiChoice": bool(any("," in value or ";" in value for value in answered.head(100))),
            "values": [{"value": clean(v), "n": int(n)} for v, n in values.items()],
            "tokens": [{"value": clean(v), "n": int(n)} for v, n in tokens.items()],
        })
    return {
        "rows": int(len(df)),
        "columnCount": int(len(df.columns)),
        "duplicates": int(df.astype(str).duplicated().sum()),
        "columns": columns,
    }


class DatasetStore:
    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}

    def register(self, filename: str, raw: bytes) -> dict[str, Any]:
        df = read_table(filename, raw)
        if df.empty:
            raise ValueError("回答行がありません")
        df.columns = [clean(c) or f"列{i + 1}" for i, c in enumerate(df.columns)]
        dataset_id = "ds-" + _hash(raw)[:12]
        summary = inspect_dataset(df)
        meta = {
            "id": dataset_id,
            "filename": Path(filename).name,
            "hash": _hash(raw),
            **summary,
        }
        self._items[dataset_id] = {"df": df, "meta": meta}
        return meta

    def get(self, dataset_id: str) -> pd.DataFrame:
        if dataset_id not in self._items:
            raise KeyError("データがメモリ上にありません。もう一度ファイルを選択してください")
        return self._items[dataset_id]["df"]

    def meta(self, dataset_id: str) -> dict[str, Any]:
        if dataset_id not in self._items:
            raise KeyError("データが見つかりません")
        return self._items[dataset_id]["meta"]


def normalize(text: str) -> str:
    return re.sub(r"[\s　_x000a_（）()\[\]【】?？.,、。:：/\\]", "", clean(text).lower())


def short_label(label: str, limit: int = 42) -> str:
    first = re.split(r"[\n\r]", label)[0].strip()
    first = re.sub(r"_x000a_", " ", first, flags=re.I)
    return first if len(first) <= limit else first[: limit - 1] + "…"


TOPICS = {
    "age": (["年代", "年齢", "age"], ["年代", "年齢", "age group"]),
    "gender": (["性別", "gender"], ["性別", "gender"]),
    "residence": (["居住", "都道府県", "住まい"], ["居住", "都道府県", "住まい", "prefecture"]),
    "purpose": (["目的", "何をした", "過ごし"], ["目的", "理由や目的", "purpose of your visit"]),
    "source": (["情報源", "参考", "sns"], ["情報源", "入手先", "参考にする", "source of information"]),
    "barrier": (["障壁", "行かなかった", "ためらう", "理由"], ["障壁", "行かなかった理由", "ためらう理由", "reason"]),
    "cost": (["費用", "金額", "予算", "コスト"], ["費用", "宿泊費", "飲食費", "交通費", "cost"]),
    "weekday": (["平日", "曜日"], ["平日", "曜日", "weekday"]),
    "satisfaction": (["満足", "満足度"], ["満足", "満足度", "satisfied"]),
    "revisit": (["再訪", "また行きたい"], ["再訪", "また行きたい", "visit again"]),
    "companion": (["同行", "誰と"], ["同行者", "誰と", "間柄", "who did you visit with"]),
    "stay": (["宿泊", "日帰り"], ["宿泊数", "日帰り", "宿泊", "overnight"]),
}


def _topic_hits(question: str) -> list[str]:
    q = normalize(question)
    return [key for key, (query_words, _) in TOPICS.items() if any(normalize(w) in q for w in query_words)]


def _choice_index(meta: dict[str, Any] | None) -> dict[str, str]:
    """列名→「列名＋選択肢の値」の検索用テキスト。

    以前は列名だけを照合していたため、「お金がない」のように *選択肢* に現れる語で
    質問されると 0 点になり、対応する列を見つけられなかった。inspect_dataset が
    列ごとの値・トークンを既に持っているので、それを検索対象に含める。
    """
    index: dict[str, str] = {}
    for column in (meta or {}).get("columns", []):
        name = column.get("name", "")
        parts = [name]
        parts += [str(item.get("value", "")) for item in column.get("values", [])[:12]]
        parts += [str(item.get("value", "")) for item in column.get("tokens", [])[:12]]
        index[name] = normalize(" ".join(parts))
    return index


# 照合ノイズになる語（助詞・機能語）。n-gramで拾ってしまうので除く。
_STOP_FRAGMENTS = {
    "こと", "もの", "ため", "どの", "くらい", "どのくらい", "ですか", "ますか", "でしょう",
    "した", "して", "いる", "ある", "ない", "なる", "れる", "られ", "とき", "ところ",
    "について", "における", "という", "そして", "また", "など", "たち", "人は", "のは",
    "には", "とは", "から", "まで", "より", "ほど", "だけ", "しか", "でも", "ても",
}


# 同じ意味なのに表記が違う語。設問文は「訪れた」、学生の問いは「訪問」のように
# ずれるため、問い側に出た語を設問側の言い回しへ広げてから照合する。
SYNONYMS = {
    "訪問": ["訪れ", "来訪", "行った"],
    "来訪": ["訪れ", "訪問"],
    "再訪": ["また行き", "もう一度"],
    "認知": ["知って", "聞いたこと"],
    "検討": ["考えた", "行こうと"],
    "障壁": ["行かなかった理由", "できなかった理由", "ためらう"],
    "費用": ["お金", "金額", "予算", "料金"],
    "お金": ["費用", "金額", "予算"],
    "意向": ["思いますか", "したいですか", "行きたい"],
    "来る": ["行きたい", "訪れ", "来訪"],
    "あったら": ["あれば", "したいこと", "してみたい"],
    "あれば": ["したいこと", "してみたい"],
    "属性": ["学年", "性別", "年代"],
    "学年": ["学部", "院生", "何年"],
}


# 「どのくらいですか」のような疑問形・言い回しは、内容ではなく文の形。
# 設問文にも同じ言い回しが出るため、n-gramで拾うと無関係な設問に当たってしまう
# （例:「…どのくらいか」が「今、旅行に行きたい気持ちはどのくらいですか」に一致）。
_FUNCTION_PHRASES = (
    "はどのくらいですか", "どのくらいですか", "どのくらい", "どのような", "どういう",
    "ですか", "ますか", "でしょうか", "でしたか", "ください", "教えてください",
    "について", "における", "ということ", "というもの", "しているか", "しますか",
    "は何か", "は何ですか", "が多いか", "が多い", "でしょう", "でした",
)


def _is_function_fragment(fragment: str) -> bool:
    """内容語ではなく文の形（疑問形・敬体）の一部なら True。"""
    return any(fragment in phrase for phrase in _FUNCTION_PHRASES)


def _fragments(text: str) -> list[str]:
    """日本語は分かち書きが無いため、2〜4文字のn-gramで照合する。

    以前は正規表現 [一-龥ぁ-んァ-ヶ]{2,} で切り出していたが、日本語は語の境界に
    区切り文字が無いため *問い全体が1トークン* になり、どの列名にも一致しなかった。
    """
    base = normalize(text)
    fragments: list[str] = []
    for size in (4, 3, 2):
        for start in range(len(base) - size + 1):
            fragment = base[start:start + size]
            if fragment in _STOP_FRAGMENTS or _is_function_fragment(fragment):
                continue
            if re.fullmatch(r"[ぁ-ん]{2}", fragment):   # 「がな」「ての」等の助詞片は捨てる
                continue
            fragments.append(fragment)
    return list(dict.fromkeys(fragments))


def _synonym_fragments(text: str) -> list[str]:
    """問いに出た語を、設問側の言い回しへ広げた候補。

    直接一致より弱く扱う。同点にすると「学年」の同義語『院生』が、
    実際に「学年」を含む設問より上に来てしまうため。
    """
    base = normalize(text)
    expanded: list[str] = []
    for word, alternatives in SYNONYMS.items():
        if word in base:
            expanded.extend(normalize(alt) for alt in alternatives)
    return list(dict.fromkeys(expanded))


def _doc_freq(fragment: str, choice_index: dict[str, str]) -> int:
    """その語が何列に出るか。多くの列に出る語ほど、列を見分ける力が無い。"""
    return sum(1 for text in choice_index.values() if fragment in text)


def _is_noise(fragment: str, choice_index: dict[str, str]) -> bool:
    """調査票全体に出る語（例：熱海調査の「熱海」）は識別に使わない。

    これを入れないと、ほぼ全列に含まれる語が全列へ一律に加点し、
    本命の一致が無いときにその点数だけで順位が決まってしまう。
    """
    total = len(choice_index)
    if total < 4:
        return False
    return _doc_freq(fragment, choice_index) > total / 2


def _column_score(column: str, question: str, prefer: list[str] | None = None,
                  choice_index: dict[str, str] | None = None) -> int:
    c = normalize(column)
    index = choice_index or {}
    # 選択肢の値まで含めた検索対象（無ければ列名のみ＝従来動作）
    haystack = index.get(column, c)
    score = 0
    for topic in _topic_hits(question):
        if any(normalize(alias) in c for alias in TOPICS[topic][1]):
            score += 20
    for fragment in _fragments(question):
        if index and _is_noise(fragment, index):
            continue
        if fragment in c:
            # 設問文に出る語が最も強い根拠。出現列が少ないほどさらに強い
            rarity = 2 if index and _doc_freq(fragment, index) == 1 else 1
            score += len(fragment) * 2 * rarity
        elif fragment in haystack and len(fragment) >= 3:
            # 選択肢は自由記述的で偶然かすることが多い。希少加点はせず、
            # 2文字の偶然一致（「熱海に」の“に”のような助詞込み）も採らない。
            score += len(fragment)
    for fragment in _synonym_fragments(question):
        if index and _is_noise(fragment, index):
            continue
        if fragment in c:
            score += len(fragment) * 2            # 言い換えでも設問文への一致は強い根拠
        elif fragment in haystack:
            score += 1
    for alias in prefer or []:
        if normalize(alias) in c:
            score += 28
    if "タイムスタンプ" in column or normalize(column) in ("timestamp",):
        score -= 100
    return score


def _rank_columns(df: pd.DataFrame, question: str, prefer: list[str] | None = None,
                  choice_index: dict[str, str] | None = None) -> list[str]:
    scored = [(str(col), _column_score(str(col), question, prefer, choice_index),
               int(df[col].notna().sum())) for col in df.columns]
    scored = [item for item in scored if item[1] > 0]
    scored.sort(key=lambda item: (item[1], item[2]), reverse=True)
    return [name for name, _, _ in scored]


# 「AによってBは違うか」「A別のB」のように、比較軸(A)と分析対象(B)を助詞から取り出す。
# 日本語の分析依頼は型が少ないので、パターンで十分に拾える（LLM不使用）。
_COMPARISON_PATTERNS = [
    re.compile(r"(?P<group>.{2,30}?)(?:によって|により|によ)(?P<metric>.{2,40}?)(?:は|が|に)?(?:違|異な|差)"),
    re.compile(r"(?P<group>.{2,30}?)(?:別|ごと|毎)(?:に|の|で)?(?P<metric>.{2,40})"),
    re.compile(r"(?P<group>.{2,30}?)(?:で|が)(?P<metric>.{2,40}?)(?:は|が)(?:違|異な|変わ|差)"),
    re.compile(r"(?P<metric>.{2,40}?)(?:は)(?P<group>.{2,30}?)(?:によって|により|で)(?:違|異な|変わ)"),
]


def _extract_comparison(question: str) -> tuple[str, str]:
    """比較軸と分析対象の手がかり語を返す。取れなければ ("", "")。"""
    for pattern in _COMPARISON_PATTERNS:
        match = pattern.search(question)
        if match:
            group = clean(match.group("group"))
            metric = clean(match.group("metric"))
            if group and metric:
                return group, metric
    return "", ""


def _matching_columns(df: pd.DataFrame, aliases: list[str]) -> list[str]:
    found = []
    for col in df.columns:
        normalized = normalize(str(col))
        if any(normalize(alias) in normalized for alias in aliases):
            found.append(str(col))
    return sorted(found, key=lambda col: int(df[col].notna().sum()), reverse=True)


def _column_info(meta: dict[str, Any], name: str) -> dict[str, Any]:
    return next((col for col in meta["columns"] if col["name"] == name), {"name": name, "shortName": short_label(name)})


def build_plan(df: pd.DataFrame, meta: dict[str, Any], question: str) -> dict[str, Any]:
    question = clean(question)
    if len(question) < 5:
        raise ValueError("知りたいことを、5文字以上で入力してください")
    q = normalize(question)

    journey_requested = any(word in q for word in ("段階", "訪問に至", "離脱", "検討した", "選ばれない"))
    visit_cols = _matching_columns(df, ["これまでに熱海市", "訪れたことがありますか", "ever visited atami"])
    awareness_cols = _matching_columns(df, ["名前を聞いたこと", "heard ofatami"])
    consideration_cols = _matching_columns(df, ["行こうと考えたこと", "thought about going to atami"])
    barrier_cols = _matching_columns(df, ["行こうと思ったのに行かなかった理由", "reasons why you decided not to go"])
    if journey_requested and visit_cols and awareness_cols and consideration_cols:
        chosen = {
            "visitColumns": visit_cols,
            "awarenessColumn": awareness_cols[0],
            "considerationColumn": consideration_cols[0],
            "barrierColumn": barrier_cols[0] if barrier_cols else "",
        }
        return {
            "question": question,
            "type": "journey",
            "label": "訪問段階の構成分析",
            "status": "ready",
            "confidence": "high",
            "reason": "訪問経験・認知・検討を測る設問が見つかったため、未訪問者を段階別に整理します。",
            "columns": chosen,
            "columnInfo": {
                key: [_column_info(meta, value) for value in values] if isinstance(values, list) else _column_info(meta, values)
                for key, values in chosen.items() if values
            },
            "approvalNote": "同じ回答者の時系列追跡ではないため、『離脱率』ではなく回答時点の構成として解釈します。",
        }

    choice_index = _choice_index(meta)

    group_prefer: list[str] = []
    if any(word in q for word in ("年代", "年齢", "若者", "高齢")):
        group_prefer = ["年代", "年齢", "age group"]
    elif any(word in q for word in ("性別", "男女")):
        group_prefer = ["性別", "gender"]
    elif any(word in q for word in ("居住", "都道府県")):
        group_prefer = ["居住", "都道府県", "prefecture"]
    elif any(word in q for word in ("訪問経験", "訪問した", "未訪問")):
        group_prefer = ["訪れたことがありますか", "ever visited"]

    # 「AによってBは違うか」の形が取れたら、そのA（比較軸）を最優先で使う。
    # 問い全体から属性語を拾うと、対象側(B)の語を軸と誤認する（例:「学年別の“訪問経験”」で
    # 訪問経験を軸にしてしまう）ため、抽出できた手がかりを先に当てる。
    group_hint, metric_hint = _extract_comparison(question)
    group_candidates: list[str] = []
    if group_hint:
        group_candidates = _rank_columns(df, group_hint, None, choice_index)
    if not group_candidates and group_prefer:
        # prefer は加点ではなく「その語を含む列だけ」に絞り込む。問い全体で採点すると
        # 対象側(B)の語が効いて、軸に関係ない列が1位になってしまうため。
        allowed = set(_matching_columns(df, group_prefer))
        group_candidates = [col for col in _rank_columns(df, question, group_prefer, choice_index)
                            if col in allowed]
    group_column = group_candidates[0] if group_candidates else ""

    # 対象語が取れていれば、そちらを優先して分析対象を決める
    metric_source = metric_hint or question
    metric_candidates = _rank_columns(df, metric_source, None, choice_index)
    if not metric_candidates and metric_hint:
        metric_candidates = _rank_columns(df, question, None, choice_index)
    metric_candidates = [col for col in metric_candidates if col != group_column]

    if not metric_candidates:
        return {
            "question": question,
            "type": "unavailable",
            "label": "現データでは分析対象を特定できません",
            "status": "needs-input",
            "confidence": "low",
            "reason": "知りたい内容に対応する列を自動で見つけられませんでした。列を人が選択してください。",
            "columns": {},
            "columnOptions": meta["columns"],
        }

    compare_requested = bool(group_column) and (
        bool(group_hint) or any(word in q for word in ("違い", "違う", "比較", "別", "ごと", "によって", "傾向", "差")))
    analysis_type = "crosstab" if compare_requested else "frequency"
    selected_metrics = metric_candidates[:2] if analysis_type == "crosstab" else metric_candidates[:1]
    used = _used_questions(meta, group_column, selected_metrics, question, choice_index)
    return {
        "question": question,
        "type": analysis_type,
        "label": "グループ別の構成比比較" if analysis_type == "crosstab" else "回答構成の集計",
        "status": "ready",
        "confidence": "medium",
        "reason": "知りたいことに含まれる語と設問名の対応から、使用する列を提案しました。実行前に変更できます。",
        "columns": {"groupColumn": group_column, "metricColumns": selected_metrics},
        "columnInfo": {
            "groupColumn": _column_info(meta, group_column) if group_column else None,
            "metricColumns": [_column_info(meta, col) for col in selected_metrics],
        },
        "columnOptions": meta["columns"],
        "usedQuestions": used,
        "approvalNote": "以下の列と分析方法を確認してから実行してください。",
    }


def _why_chosen(column: dict[str, Any], question: str, choice_index: dict[str, str]) -> str:
    """その設問を選んだ理由を、根拠が見える形の1行で返す。"""
    name = column.get("name", "")
    c = normalize(name)
    hits_name, hits_choice = [], []
    # 最長一致を優先したいので、長いn-gramから見て包含される短い断片は捨てる。
    # 全列に出る語（「熱海」等）は理由として無意味なので挙げない。
    for fragment in _fragments(question) + _synonym_fragments(question):
        if choice_index and _is_noise(fragment, choice_index):
            continue
        if fragment in c:
            if not any(fragment in seen for seen in hits_name):
                hits_name.append(fragment)
        elif fragment in choice_index.get(name, ""):
            if not any(fragment in seen for seen in hits_choice):
                hits_choice.append(fragment)
    # n-gramは重なるので、長い順に最大2件だけ見せる（「平日に熱・日に熱海」のような羅列を避ける）
    def _top(hits: list[str]) -> str:
        return "・".join(sorted(hits, key=len, reverse=True)[:2])

    if hits_name:
        return f"設問文に「{_top(hits_name)}」が含まれるため"
    if hits_choice:
        return f"選択肢に「{_top(hits_choice)}」が含まれるため"
    return "知りたいことと関連する設問のため"


def _used_questions(meta: dict[str, Any], group_column: str, metric_columns: list[str],
                    question: str, choice_index: dict[str, str]) -> list[dict[str, Any]]:
    """「使う設問はこれです」として画面に出す一覧。役割と選定理由を添える。"""
    items = []
    if group_column:
        info = _column_info(meta, group_column)
        items.append({
            "role": "比較軸", "name": group_column,
            "shortName": info.get("shortName", group_column),
            "answered": info.get("nonEmpty"),
            "reason": _why_chosen(info, question, choice_index),
        })
    for column in metric_columns:
        info = _column_info(meta, column)
        items.append({
            "role": "分析対象", "name": column,
            "shortName": info.get("shortName", column),
            "answered": info.get("nonEmpty"),
            "reason": _why_chosen(info, question, choice_index),
        })
    return items


def _first_answer(df: pd.DataFrame, columns: list[str]) -> pd.Series:
    valid = [col for col in columns if col in df.columns]
    if not valid:
        return pd.Series("", index=df.index)
    frame = pd.concat([df[col].map(clean).replace("", pd.NA) for col in valid], axis=1)
    return frame.bfill(axis=1).iloc[:, 0].fillna("").map(clean)


def _positive(series: pd.Series, kind: str) -> pd.Series:
    normalized = series.map(normalize)
    negative = normalized.str.contains(r"(?:^|[^a-z])(?:no|never)(?:[^a-z]|$)|ない|知らない|いいえ|not", regex=True)
    if kind == "awareness":
        positive = normalized.str.contains(r"知って|聞いたことがある|よく知って|heardof", regex=True)
    elif kind == "consideration":
        positive = normalized.str.contains(r"考えたことがある|thoughtabout", regex=True)
    else:
        positive = normalized.str.contains(r"ある|はい|yes|visited", regex=True)
    return (series != "") & positive & ~negative


def _barrier_counts(series: pd.Series) -> list[dict[str, Any]]:
    counts = _tokens(series).value_counts().head(8)
    return [{"label": clean(value), "n": int(n)} for value, n in counts.items()]


def analyze_journey(df: pd.DataFrame, plan: dict[str, Any]) -> dict[str, Any]:
    columns = plan["columns"]
    visit = _first_answer(df, columns.get("visitColumns", []))
    awareness = df[columns["awarenessColumn"]].map(clean)
    consideration = df[columns["considerationColumn"]].map(clean)
    visit_yes = _positive(visit, "visit")
    visit_answered = visit != ""
    not_visited = visit_answered & ~visit_yes
    aware = _positive(awareness, "awareness")
    considered = _positive(consideration, "consideration")

    segments = [
        {"id": "visited", "label": "訪問済み", "n": int(visit_yes.sum()), "definition": "熱海への訪問経験あり"},
        {"id": "considered", "label": "未訪問・検討済み", "n": int((not_visited & considered).sum()), "definition": "未訪問だが、行こうと考えたことがある"},
        {"id": "aware", "label": "未訪問・認知のみ", "n": int((not_visited & aware & ~considered).sum()), "definition": "熱海を知っているが、検討経験はない"},
        {"id": "unaware", "label": "未訪問・未認知", "n": int((not_visited & ~aware).sum()), "definition": "熱海を知らず、訪問経験もない"},
    ]
    classified = sum(row["n"] for row in segments)
    unknown = int(len(df) - classified)
    if unknown:
        segments.append({"id": "unknown", "label": "判定不能", "n": unknown, "definition": "必要な設問が未回答"})
    for row in segments:
        row["share"] = round(row["n"] * 100 / len(df), 1) if len(df) else 0.0

    barrier_col = columns.get("barrierColumn")
    barriers: list[dict[str, Any]] = []
    if barrier_col and barrier_col in df.columns:
        barriers = _barrier_counts(df.loc[not_visited & considered, barrier_col])

    focus = next(row for row in segments if row["id"] == "considered")
    top_barrier = barriers[0]["label"] if barriers else "訪問に至らない理由"
    claims = [{
        "text": f"回答者{len(df)}人のうち、熱海を検討したが未訪問の回答者は{focus['n']}人（{focus['share']}%）でした。",
        "evidence": f"訪問経験設問＋検討経験設問 / 分子 {focus['n']}人・分母 {len(df)}人",
    }]
    if barriers:
        claims.append({
            "text": f"この層で最も多く選ばれた訪問しなかった理由は「{top_barrier}」でした。",
            "evidence": f"未訪問・検討済み層の理由設問 / 選択数 {barriers[0]['n']}件（複数回答を含む）",
        })
    next_questions = [
        {
            "text": f"「{top_barrier}」と感じた具体的な場面を教えてください。",
            "purpose": "障壁の内容を具体化し、情報不足と魅力不足を区別する",
            "type": "複数選択＋その他",
        },
        {
            "text": "必要な費用・所要時間・過ごし方が事前に分かる場合、熱海への訪問意向はどの程度高まりますか。",
            "purpose": "情報設計によって訪問意向が変わる仮説を確認する",
            "type": "5段階評価",
        },
    ]
    return {
        "type": "journey",
        "headline": f"未訪問でも検討経験がある層は {focus['n']}人",
        "segments": segments,
        "barriers": barriers,
        "claims": claims,
        "canSay": ["この回答者群の中で、訪問経験・認知・検討経験がどのように分かれているか。"],
        "cannotSay": [
            "同一人物を時系列で追跡していないため、段階間の『離脱率』とは断定できません。",
            "大学生全体や観光客全体にも同じ割合が当てはまるとは言えません。",
            "提示した情報施策によって実際の訪問が増えるとは、まだ言えません。",
        ],
        "nextQuestions": next_questions,
        "trace": [
            {"role": "訪問経験", "columns": columns.get("visitColumns", [])},
            {"role": "認知", "columns": [columns.get("awarenessColumn", "")]},
            {"role": "検討経験", "columns": [columns.get("considerationColumn", "")]},
            {"role": "未訪問理由", "columns": [barrier_col] if barrier_col else []},
        ],
    }


def _frequency(df: pd.DataFrame, column: str) -> dict[str, Any]:
    if column not in df.columns:
        raise ValueError(f"列『{column}』が見つかりません")
    series = df[column].map(clean)
    answered = series[series != ""]
    is_multi = any("," in value or ";" in value for value in answered.head(100))
    # 選択肢の少ない通常アンケートでは、上位だけに切らず全回答項目を返す。
    # 「その他」のような少数回答も、施策検討では重要になり得るため省略しない。
    counts = (_tokens(series) if is_multi else answered).value_counts()
    rows = []
    for value, n in counts.items():
        rows.append({
            "label": clean(value),
            "n": int(n),
            "denominator": int(len(answered)),
            "share": round(int(n) * 100 / len(answered), 1) if len(answered) else 0.0,
        })
    return {"column": column, "shortName": short_label(column), "answered": int(len(answered)), "missing": int(len(df) - len(answered)), "multi": is_multi, "rows": rows}


def _crosstab(df: pd.DataFrame, group_column: str, metric_column: str) -> dict[str, Any]:
    if group_column not in df.columns or metric_column not in df.columns:
        raise ValueError("選択された列がデータにありません")
    groups = df[group_column].map(clean)
    metrics = df[metric_column].map(clean)
    group_values = groups[groups != ""].value_counts().head(6).index.tolist()
    metric_tokens = _tokens(metrics)
    # 全体上位だけに限定すると、特定年代だけが選んだ少数回答が画面から消える。
    # そのため、実際に1件以上あった選択肢はすべて返す。
    token_values = metric_tokens.value_counts().index.tolist()
    is_multi = any("," in value or ";" in value for value in metrics[metrics != ""].head(100))
    group_summaries = []
    for group in group_values:
        group_mask = groups == group
        answered_mask = group_mask & (metrics != "")
        answered_values = metrics[answered_mask]
        selections = int(len(_tokens(answered_values))) if is_multi else int(len(answered_values))
        group_summaries.append({
            "group": clean(group),
            "respondents": int(group_mask.sum()),
            "answered": int(answered_mask.sum()),
            "selections": selections,
            "missing": int((group_mask & (metrics == "")).sum()),
        })
    rows = []
    best_gap = {"gap": -1.0}
    for token in token_values:
        cells = []
        shares = []
        for group in group_values:
            base = (groups == group) & (metrics != "")
            if is_multi:
                hit = metrics.map(lambda value: token in [p.strip() for p in MULTI_SEP.split(value) if p.strip()])
            else:
                hit = metrics == token
            denominator = int(base.sum())
            n = int((base & hit).sum())
            share = round(n * 100 / denominator, 1) if denominator else 0.0
            cells.append({"group": clean(group), "n": n, "denominator": denominator, "share": share})
            if denominator:
                shares.append((share, clean(group), n, denominator))
        if shares:
            high = max(shares)
            low = min(shares)
            gap = high[0] - low[0]
            if gap > best_gap["gap"]:
                best_gap = {"gap": round(gap, 1), "token": clean(token), "high": high, "low": low}
        rows.append({"label": clean(token), "cells": cells})
    return {
        "groupColumn": group_column,
        "metricColumn": metric_column,
        "groupLabel": short_label(group_column),
        "metricLabel": short_label(metric_column),
        "groups": [clean(value) for value in group_values],
        "groupSummaries": group_summaries,
        "rows": rows,
        "multi": is_multi,
        "bestGap": best_gap,
    }


def run_analysis(df: pd.DataFrame, meta: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    analysis_type = plan.get("type")
    question = clean(plan.get("question"))
    if analysis_type == "journey":
        result = analyze_journey(df, plan)
        result.update({"question": question, "rows": len(df), "duplicates": meta["duplicates"]})
        return result

    columns = plan.get("columns", {})
    metrics = [col for col in columns.get("metricColumns", []) if col]
    if not metrics:
        raise ValueError("分析する設問を1つ以上選んでください")
    analyses = []
    claims = []
    limitations = ["結果はこのアンケートの回答者内の傾向であり、母集団全体へそのまま一般化できません。"]
    if analysis_type == "crosstab":
        group = columns.get("groupColumn", "")
        if not group:
            raise ValueError("比較に使うグループ設問を選んでください")
        for metric in metrics[:2]:
            table = _crosstab(df, group, metric)
            analyses.append(table)
            gap = table.get("bestGap", {})
            if gap.get("gap", -1) >= 0:
                high = gap["high"]
                low = gap["low"]
                claims.append({
                    "text": f"『{gap['token']}』の回答割合は、{high[1]}が{high[0]}%、{low[1]}が{low[0]}%で、回答者内では{gap['gap']}ポイントの差がありました。",
                    "evidence": f"{short_label(metric)} × {short_label(group)} / {high[2]}/{high[3]}人 と {low[2]}/{low[3]}人",
                })
            if any(cell["denominator"] < 30 for row in table["rows"] for cell in row["cells"] if cell["denominator"]):
                limitations.append("回答者30人未満のグループを含むため、差は探索的な傾向として扱ってください。")
        headline = claims[0]["text"] if claims else "グループ別の回答構成を算出しました"
    else:
        for metric in metrics[:1]:
            table = _frequency(df, metric)
            analyses.append(table)
            if table["rows"]:
                top = table["rows"][0]
                claims.append({
                    "text": f"最も多い回答は『{top['label']}』で、{top['n']}件（回答者ベース{top['share']}%）でした。",
                    "evidence": f"{table['shortName']} / 分子 {top['n']}件・回答者 {top['denominator']}人",
                })
        headline = claims[0]["text"] if claims else "回答構成を算出しました"

    next_questions = [
        {"text": f"この回答を選んだ最も大きな理由を教えてください。", "purpose": "集計結果の背景を確認する", "type": "単一選択＋その他"},
        {"text": "条件が変わった場合、行動意向はどの程度変わりますか。", "purpose": "施策候補による意向差を確認する", "type": "5段階評価"},
    ]
    return {
        "type": analysis_type,
        "question": question,
        "rows": len(df),
        "duplicates": meta["duplicates"],
        "headline": headline,
        "analyses": analyses,
        "claims": claims,
        "canSay": ["このアンケートの回答者内で、回答構成やグループ間の構成比がどう異なるか。"],
        "cannotSay": list(dict.fromkeys(limitations + ["観測された差の原因や、施策による効果までは断定できません。"])),
        "nextQuestions": next_questions,
        "trace": [{"role": "比較軸", "columns": [columns.get("groupColumn", "")]}, {"role": "分析対象", "columns": metrics}],
    }
