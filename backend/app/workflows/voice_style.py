"""会話モード（声で話して声で聞く）の回答の決まりと、回答傾向。

2026-11-19 利用者「会話モードでは会話に即した回答に。会話に不要な絵文字や箇条書きは控えめに。一般的な会話を想定して
条件を決めて」「回答傾向を複数から選べるように（フレンドリーなど）」。回答は読み上げる（Supertonic）前提。
指示は文脈の最後ほど効くので、言語の指示（language_guard）と同じく最後の利用者の発言に書き添える（送る分だけ。保存しない）。"""
from __future__ import annotations

# 一般的な会話を想定した決まり（どの傾向でも守る）
SPOKEN_RULES = (
    "これは声の会話です。返答はそのまま音声で読み上げられます。次を守ってください。"
    "絵文字・顔文字・記号の飾りを使わない。箇条書き・見出し・表・コード・太字などの書式を使わず、つながった話し言葉で話す。"
    "短く、2〜4 文を目安にする。長い説明が要るときは要点を先に言い、続きを聞きたいか尋ねる。"
    "URL・出典の番号・括弧の注記は言わない。数字や単位は声で聞き取りやすく言う。"
    "相手が話した言語で答える（英語で話されたら英語で）。"
    "音声認識の誤りで言葉が不自然なときは、意味を推し量って答えるか、短く聞き返す。"
    "質問を返すのは 1 回の返答で 1 つまで。"
)

STYLES: dict[str, tuple[str, str]] = {
    "friendly": ("フレンドリー", "親しい友人のように、くだけた温かい口調で話す。相づちや共感を自然に入れる。"),
    "polite": ("丁寧", "です・ます調で、落ち着いた丁寧な口調で話す。"),
    "concise": ("簡潔", "要点だけを 1〜2 文で答える。前置きや繰り返しをしない。"),
    "teacher": ("英会話の先生", "英会話の練習相手になる。相手が英語で話したら、やさしい英語で短く答え、会話が続く質問を 1 つ返す。"
                "英語に誤りや不自然な言い方があれば、返答の最後に一言だけ自然な言い方を示す。日本語で聞かれたら日本語で短く説明する。"),
    "listener": ("聞き役", "相手の話をよく聞き、共感と短い質問を中心にする。助言は求められたときだけ短く言う。"),
}
DEFAULT_STYLE = "friendly"


def voice_note(style: str) -> str:
    label_hint = STYLES.get(style) or STYLES[DEFAULT_STYLE]
    return f"\n\n（{SPOKEN_RULES}話し方: {label_hint[1]}）"


def voice_guard(history: list[dict], style: str) -> list[dict]:
    """会話モードの発言なら、最後の利用者の発言に決まりと傾向を書き添える。会話モードでなければそのまま。"""
    if not style:
        return history
    note = voice_note(style)
    for index in range(len(history) - 1, -1, -1):
        message = history[index]
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return [*history[:index], {**message, "content": content + note}, *history[index + 1:]]
        if isinstance(content, list):   # 画像つき（VLM）: 文字の部分に足す
            parts = [dict(part) for part in content]
            for part in parts:
                if part.get("type") == "text":
                    part["text"] = str(part.get("text", "")) + note
                    return [*history[:index], {**message, "content": parts}, *history[index + 1:]]
        return history
    return history
