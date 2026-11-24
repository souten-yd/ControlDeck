"""ニュースのソースのプリセット（分野ごとの無料のフィード・Release・フォーラム）。画面（設定 → ニュース → ソース →「プリセット」）はこの一覧を読む。

2026-11-23 利用者「ニュースのソースの右側にプリセットボタン…RSS・フォーラム・ニュースのソースを準備…ブルームバーグとかは有料だと思うんで、基本的には無料のものを優先」
「AI の情報、特にローカル AI 向けの情報を強力に欲してる」。

- 入れたもの: ログイン・購読が要らず、実際に GET して RSS/Atom（GitHub は API、Discourse は latest.json）として読めたもの。確かめた日は VERIFIED。
  リダイレクトを追わない取得（knowledge.sources.http_get）で読める最終の URL だけを入れた（NHK・Gizmodo など）。
- 入れなかったもの: 購読が要る・読める量が絞られる（Bloomberg・WSJ・日経・NYT・Washington Post・Economist・MIT Technology Review・Seeking Alpha など）、
  RSS が無い・404（r/LocalLLaMA の .rss は 404/429 だったので、Reddit は既存の API キーの仕組みで取る）。
- 追加の操作はソースの設定（sources.json）へ足すだけ。種類は sources.normalize が読む形: rss=URL、github/codeberg=owner/repo、discourse=ドメイン、reddit=subreddit。
"""
from __future__ import annotations

VERIFIED = "2026-11-23"
VERIFIED_2 = "2026-11-24"      # 2 回目に足したもの（本番と同じ取得 http_get で、RSS として 1 件以上読めた日）

GROUPS: list[dict] = [
    {"id": "stock", "label": "株式・市場", "hint": "市況・企業・中央銀行・金融当局"},
    {"id": "science", "label": "科学技術", "hint": "科学・宇宙・テクノロジー全般"},
    {"id": "game", "label": "テレビゲーム", "hint": "国内外のゲームのニュース"},
    {"id": "pc", "label": "PC", "hint": "PC・ハードウェア・自作・Linux"},
    {"id": "electronics", "label": "電気・電子", "hint": "半導体・電子工作・エネルギー"},
    {"id": "ai", "label": "AI", "hint": "AI 全般（研究所・企業・日本語の解説）"},
    {"id": "local-ai", "label": "ローカル AI", "hint": "llama.cpp・ollama・vLLM などの更新、Hugging Face、コミュニティ"},
    {"id": "xr", "label": "VR・AR・ゲーム開発", "hint": "Quest・SteamVR・Unity など"},
    {"id": "dev", "label": "開発", "hint": "企業のテックブログ・開発者向けの新着"},
    {"id": "jp-politics", "label": "日本の政治経済", "hint": "国内の報道・省庁・日銀"},
    {"id": "world-politics", "label": "海外の政治経済", "hint": "海外の報道・国際機関"},
]


def _rss(group: str, name: str, url: str, lang: str = "ja", note: str = "", verified: str = "") -> dict:
    return {"group": group, "kind": "rss", "name": name, "value": url, "lang": lang, "note": note, "key": False, "verified": verified or VERIFIED}


def _n(group: str, name: str, url: str, lang: str = "ja", note: str = "") -> dict:
    """2026-11-24 に足したもの（参考の note の記事・daige.st の一覧・日本の AI/政治のメディア）。"""
    return _rss(group, name, url, lang, note, VERIFIED_2)


def _gh(group: str, repo: str, note: str = "") -> dict:
    return {"group": group, "kind": "github", "name": repo, "value": repo, "lang": "en", "note": note or "Release", "key": False}


def _forum(group: str, domain: str, name: str, lang: str = "en") -> dict:
    return {"group": group, "kind": "discourse", "name": name, "value": domain, "lang": lang, "note": "フォーラム（最新の話題）", "key": False}


def _reddit(group: str, sub: str) -> dict:
    return {"group": group, "kind": "reddit", "name": f"r/{sub}", "value": sub, "lang": "en",
            "note": "API キーが要る（設定 → API キー。RSS は 404/429 で読めなかった）", "key": True}


_ENTRIES: list[dict] = [
    # 株式・市場
    _rss("stock", "Yahoo!ニュース 経済", "https://news.yahoo.co.jp/rss/topics/business.xml"),
    _rss("stock", "東洋経済オンライン", "https://toyokeizai.net/list/feed/rss"),
    _rss("stock", "ダイヤモンド・オンライン", "https://diamond.jp/list/feed/rss/dol"),
    _rss("stock", "NHK 経済", "https://news.web.nhk/n-data/conf/na/rss/cat5.xml"),
    _rss("stock", "日本銀行 新着", "https://www.boj.or.jp/rss/whatsnew.xml", note="公式"),
    _rss("stock", "金融庁 新着", "https://www.fsa.go.jp/fsaNewsListAll_rss2.xml", note="公式"),
    _rss("stock", "CNBC Markets", "https://www.cnbc.com/id/20910258/device/rss/rss.html", "en"),
    _rss("stock", "CNBC Top News", "https://www.cnbc.com/id/100003114/device/rss/rss.html", "en"),
    _rss("stock", "MarketWatch Market Pulse", "https://feeds.content.dowjones.io/public/rss/mw_marketpulse", "en", "見出しの速報"),
    _rss("stock", "Investing.com News", "https://www.investing.com/rss/news.rss", "en"),
    _rss("stock", "Benzinga", "https://www.benzinga.com/feed", "en"),
    _rss("stock", "BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml", "en"),
    _rss("stock", "FRB（米連邦準備制度）プレスリリース", "https://www.federalreserve.gov/feeds/press_all.xml", "en", "公式"),
    _rss("stock", "ECB プレスリリース", "https://www.ecb.europa.eu/rss/press.html", "en", "公式"),
    _rss("stock", "SEC プレスリリース", "https://www.sec.gov/news/pressreleases.rss", "en", "公式"),
    _rss("stock", "はてなブックマーク 経済", "https://b.hatena.ne.jp/hotentry/economics.rss"),
    # 科学技術
    _rss("science", "ITmedia NEWS", "https://rss.itmedia.co.jp/rss/2.0/news_bursts.xml"),
    _rss("science", "Yahoo!ニュース 科学", "https://news.yahoo.co.jp/rss/topics/science.xml"),
    _rss("science", "Yahoo!ニュース IT", "https://news.yahoo.co.jp/rss/topics/it.xml"),
    _rss("science", "sorae（宇宙）", "https://sorae.info/feed"),
    _rss("science", "ナゾロジー", "https://nazology.kusuguru.co.jp/feed"),
    _rss("science", "Gizmodo Japan", "https://www.gizmodo.jp/feed/index.xml"),
    _rss("science", "マイナビニュース", "https://news.mynavi.jp/rss/index"),
    _rss("science", "Publickey", "https://www.publickey1.jp/atom.xml"),
    _rss("science", "はてなブックマーク テクノロジー", "https://b.hatena.ne.jp/hotentry/it.rss"),
    _rss("science", "Science News", "https://www.sciencenews.org/feed", "en"),
    _rss("science", "ScienceDaily", "https://www.sciencedaily.com/rss/all.xml", "en"),
    _rss("science", "Phys.org", "https://phys.org/rss-feed/", "en"),
    _rss("science", "Quanta Magazine", "https://www.quantamagazine.org/feed/", "en"),
    _rss("science", "Ars Technica", "https://feeds.arstechnica.com/arstechnica/index", "en"),
    _rss("science", "TechCrunch", "https://techcrunch.com/feed/", "en"),
    _rss("science", "The Verge", "https://www.theverge.com/rss/index.xml", "en"),
    _rss("science", "NASA", "https://www.nasa.gov/news-release/feed/", "en"),
    # テレビゲーム
    _rss("game", "4Gamer.net", "https://www.4gamer.net/rss/index.xml"),
    _rss("game", "AUTOMATON", "https://automaton-media.com/feed/"),
    _rss("game", "Game*Spark", "https://www.gamespark.jp/rss/index.rdf"),
    _rss("game", "IGN Japan", "https://jp.ign.com/feed.xml"),
    _rss("game", "GAME Watch", "https://game.watch.impress.co.jp/data/rss/1.0/gmw/feed.rdf"),
    _rss("game", "Eurogamer", "https://www.eurogamer.net/feed", "en"),
    _rss("game", "PC Gamer", "https://www.pcgamer.com/rss/", "en"),
    _rss("game", "Rock Paper Shotgun", "https://www.rockpapershotgun.com/feed", "en"),
    _rss("game", "Kotaku", "https://kotaku.com/feed", "en"),
    _rss("game", "Polygon", "https://www.polygon.com/feed/", "en"),
    _rss("game", "GameSpot", "https://www.gamespot.com/feeds/news/", "en"),
    _rss("game", "VGC", "https://www.videogameschronicle.com/feed/", "en"),
    _rss("game", "GamingOnLinux", "https://www.gamingonlinux.com/article_rss.php", "en", "Linux のゲーム"),
    _rss("game", "Nintendo Life", "https://www.nintendolife.com/feeds/latest", "en"),
    _rss("game", "Steam ニュース", "https://store.steampowered.com/feeds/news/", "en"),
    # PC
    _rss("pc", "PC Watch", "https://pc.watch.impress.co.jp/data/rss/1.0/pcw/feed.rdf"),
    _rss("pc", "AKIBA PC Hotline!", "https://akiba-pc.watch.impress.co.jp/data/rss/1.0/ah/feed.rdf"),
    _rss("pc", "ASCII.jp", "https://ascii.jp/rss.xml"),
    _rss("pc", "窓の杜", "https://forest.watch.impress.co.jp/data/rss/1.0/wf/feed.rdf"),
    _rss("pc", "ITmedia PC USER", "https://rss.itmedia.co.jp/rss/2.0/pcuser.xml"),
    _rss("pc", "Tom's Hardware", "https://www.tomshardware.com/feeds.xml", "en"),
    _rss("pc", "TechPowerUp", "https://www.techpowerup.com/rss/news", "en"),
    _rss("pc", "ServeTheHome", "https://www.servethehome.com/feed/", "en"),
    _rss("pc", "Windows Central", "https://www.windowscentral.com/feeds.xml", "en"),
    _rss("pc", "HotHardware", "https://hothardware.com/rss", "en"),
    _rss("pc", "OC3D", "https://overclock3d.net/feed/", "en"),
    _rss("pc", "Wccftech", "https://wccftech.com/feed/", "en"),
    _forum("pc", "forum.level1techs.com", "Level1Techs フォーラム"),
    _forum("pc", "discourse.ubuntu.com", "Ubuntu Discourse"),
    # 電気・電子
    _rss("electronics", "EE Times Japan", "https://rss.itmedia.co.jp/rss/2.0/eetimes.xml"),
    _rss("electronics", "MONOist", "https://rss.itmedia.co.jp/rss/2.0/monoist.xml"),
    _rss("electronics", "スマートジャパン（エネルギー）", "https://rss.itmedia.co.jp/rss/2.0/smartjapan.xml"),
    _rss("electronics", "Hackaday", "https://hackaday.com/blog/feed/", "en"),
    _rss("electronics", "Adafruit Blog", "https://blog.adafruit.com/feed/", "en"),
    _rss("electronics", "IEEE Spectrum", "https://spectrum.ieee.org/feeds/feed.rss", "en"),
    _rss("electronics", "Electronics Weekly", "https://www.electronicsweekly.com/feed/", "en"),
    _rss("electronics", "CNX Software", "https://www.cnx-software.com/feed/", "en", "組込み・ボード"),
    _rss("electronics", "Semiconductor Engineering", "https://semiengineering.com/feed/", "en"),
    _rss("electronics", "Electrek（EV・電力）", "https://electrek.co/feed/", "en"),
    _rss("electronics", "pv magazine（太陽光）", "https://www.pv-magazine.com/feed/", "en"),
    _rss("electronics", "Utility Dive（電力）", "https://www.utilitydive.com/feeds/news/", "en"),
    _rss("electronics", "Arduino Blog", "https://blog.arduino.cc/feed/", "en"),
    _rss("electronics", "Raspberry Pi ニュース", "https://www.raspberrypi.com/news/feed/", "en"),
    _rss("electronics", "Seeed Studio ブログ", "https://www.seeedstudio.com/blog/feed/", "en"),
    _forum("electronics", "forum.arduino.cc", "Arduino フォーラム"),
    # AI
    _rss("ai", "ITmedia AI+", "https://rss.itmedia.co.jp/rss/2.0/aiplus.xml"),
    _rss("ai", "AINOW", "https://ainow.ai/feed/"),
    _rss("ai", "Zenn AI", "https://zenn.dev/topics/ai/feed"),
    _rss("ai", "Zenn 生成AI", "https://zenn.dev/topics/%E7%94%9F%E6%88%90ai/feed"),
    _rss("ai", "Zenn LLM", "https://zenn.dev/topics/llm/feed"),
    _rss("ai", "Qiita AI", "https://qiita.com/tags/ai/feed"),
    _rss("ai", "Qiita LLM", "https://qiita.com/tags/llm/feed"),
    _rss("ai", "はてなブックマーク AI", "https://b.hatena.ne.jp/q/AI?mode=rss&sort=hot"),
    _rss("ai", "OpenAI ニュース", "https://openai.com/news/rss.xml", "en"),
    _rss("ai", "Anthropic ニュース（非公式の自動生成）", "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_anthropic_news.xml", "en", "公式に RSS が無いため GitHub の自動生成"),
    _rss("ai", "Google AI ブログ", "https://blog.google/innovation-and-ai/technology/ai/rss/", "en"),
    _rss("ai", "Google DeepMind", "https://deepmind.google/blog/rss.xml", "en"),
    _rss("ai", "Mistral AI", "https://mistral.ai/news/rss", "en"),
    _rss("ai", "NVIDIA ブログ", "https://blogs.nvidia.com/feed/", "en"),
    _rss("ai", "Microsoft Research", "https://www.microsoft.com/en-us/research/feed/", "en"),
    _rss("ai", "AWS Machine Learning ブログ", "https://aws.amazon.com/blogs/machine-learning/feed/", "en"),
    _rss("ai", "BAIR ブログ", "https://bair.berkeley.edu/blog/feed.xml", "en"),
    _rss("ai", "Last Week in AI", "https://lastweekin.ai/feed", "en"),
    _rss("ai", "TLDR AI", "https://tldr.tech/api/rss/ai", "en"),
    _rss("ai", "MarkTechPost", "https://www.marktechpost.com/feed/", "en"),
    _rss("ai", "The Gradient", "https://thegradient.pub/rss/", "en"),
    _rss("ai", "Latent Space", "https://latent.space/feed", "en"),
    _rss("ai", "arXiv cs.CL（言語処理）", "https://rss.arxiv.org/rss/cs.CL", "en", "論文。件数が多い"),
    _forum("ai", "community.openai.com", "OpenAI コミュニティ"),
    _forum("ai", "discuss.ai.google.dev", "Google AI フォーラム"),
    # ローカル AI
    _rss("local-ai", "Hugging Face ブログ", "https://huggingface.co/blog/feed.xml", "en"),
    _rss("local-ai", "Simon Willison", "https://simonwillison.net/atom/everything/", "en", "ローカル LLM の実測が多い"),
    _rss("local-ai", "LM Studio ブログ・更新", "https://lmstudio.ai/rss.xml", "en"),
    _rss("local-ai", "Ollama ブログ", "https://ollama.com/blog/rss.xml", "en"),
    _rss("local-ai", "Interconnects", "https://www.interconnects.ai/feed", "en"),
    _rss("local-ai", "Andrej Karpathy", "https://karpathy.bearblog.dev/feed/", "en"),
    _rss("local-ai", "Philipp Schmid", "https://www.philschmid.de/rss", "en"),
    _rss("local-ai", "Lil'Log（Lilian Weng）", "https://lilianweng.github.io/index.xml", "en"),
    _rss("local-ai", "Lobsters（AI タグ）", "https://lobste.rs/t/ai.rss", "en"),
    _rss("local-ai", "Phoronix", "https://www.phoronix.com/rss.php", "en", "ROCm・Linux のドライバ"),
    _rss("local-ai", "Zenn ollama", "https://zenn.dev/topics/ollama/feed"),
    _rss("local-ai", "Zenn ローカル LLM", "https://zenn.dev/topics/localllm/feed"),
    _rss("local-ai", "Zenn llama.cpp", "https://zenn.dev/topics/llamacpp/feed"),
    _rss("local-ai", "Zenn Hugging Face", "https://zenn.dev/topics/huggingface/feed"),
    _rss("local-ai", "Qiita ollama", "https://qiita.com/tags/ollama/feed"),
    _rss("local-ai", "Qiita llama.cpp", "https://qiita.com/tags/llama.cpp/feed"),
    _rss("local-ai", "Qiita ローカルLLM", "https://qiita.com/tags/%E3%83%AD%E3%83%BC%E3%82%AB%E3%83%ABllm/feed"),
    _forum("local-ai", "discuss.huggingface.co", "Hugging Face フォーラム"),
    _forum("local-ai", "forum.level1techs.com", "Level1Techs フォーラム"),
    _forum("local-ai", "discuss.pytorch.org", "PyTorch フォーラム"),
    _forum("local-ai", "forums.developer.nvidia.com", "NVIDIA 開発者フォーラム"),
    _reddit("local-ai", "LocalLLaMA"),
    _reddit("local-ai", "LocalLLM"),
    _reddit("local-ai", "ollama"),
    *[_gh("local-ai", r) for r in (
        "ggml-org/llama.cpp", "ollama/ollama", "vllm-project/vllm", "ikawrakow/ik_llama.cpp", "unslothai/unsloth", "sgl-project/sglang",
        "LostRuins/koboldcpp", "open-webui/open-webui", "huggingface/transformers", "huggingface/text-generation-inference",
        "ggml-org/whisper.cpp", "ggml-org/ggml", "ml-explore/mlx", "ml-explore/mlx-lm", "turboderp-org/exllamav3",
        "abetlen/llama-cpp-python", "janhq/jan", "mudler/LocalAI", "mozilla-ai/llamafile", "ModelCloud/GPTQModel",
        "Dao-AILab/flash-attention", "bitsandbytes-foundation/bitsandbytes", "huggingface/peft", "huggingface/diffusers",
        "axolotl-ai-cloud/axolotl", "hiyouga/LlamaFactory", "gpustack/gpustack", "containers/ramalama",
        "EricLBuehler/mistral.rs", "mlc-ai/mlc-llm", "Comfy-Org/ComfyUI", "ROCm/vllm", "ROCm/TheRock", "triton-lang/triton")],
    # 日本の政治経済
    _rss("jp-politics", "NHK 主要ニュース", "https://news.web.nhk/n-data/conf/na/rss/cat0.xml"),
    _rss("jp-politics", "NHK 政治", "https://news.web.nhk/n-data/conf/na/rss/cat4.xml"),
    _rss("jp-politics", "NHK 社会", "https://news.web.nhk/n-data/conf/na/rss/cat1.xml"),
    _rss("jp-politics", "Yahoo!ニュース 主要", "https://news.yahoo.co.jp/rss/topics/top-picks.xml"),
    _rss("jp-politics", "Yahoo!ニュース 国内", "https://news.yahoo.co.jp/rss/topics/domestic.xml"),
    _rss("jp-politics", "Yahoo!ニュース 共同通信", "https://news.yahoo.co.jp/rss/media/kyodonews/all.xml"),
    _rss("jp-politics", "Yahoo!ニュース 産経新聞", "https://news.yahoo.co.jp/rss/media/san/all.xml"),
    _rss("jp-politics", "Yahoo!ニュース 東洋経済", "https://news.yahoo.co.jp/rss/media/toyo/all.xml"),
    _rss("jp-politics", "朝日新聞 速報", "https://www.asahi.com/rss/asahi/newsheadlines.rdf"),
    _rss("jp-politics", "毎日新聞 速報", "https://mainichi.jp/rss/etc/mainichi-flash.rss"),
    _rss("jp-politics", "時事通信", "https://www.jiji.com/rss/ranking.rdf"),
    _rss("jp-politics", "JBpress", "https://jbpress.ismedia.jp/list/feed/rss"),
    _rss("jp-politics", "ITmedia ビジネス", "https://rss.itmedia.co.jp/rss/2.0/business.xml"),
    _rss("jp-politics", "Japan Times", "https://www.japantimes.co.jp/feed/", "en"),
    _rss("jp-politics", "財務省 新着", "https://www.mof.go.jp/news.rss", note="公式"),
    _rss("jp-politics", "内閣府 新着", "https://www.cao.go.jp/rss/news.rdf", note="公式"),
    _rss("jp-politics", "経済産業省 新着", "https://www.meti.go.jp/ml_index_release_atom.xml", note="公式"),
    _rss("jp-politics", "厚生労働省 新着", "https://www.mhlw.go.jp/stf/news.rdf", note="公式"),
    _rss("jp-politics", "はてなブックマーク 世の中", "https://b.hatena.ne.jp/hotentry/social.rss"),
    # 海外の政治経済
    _rss("world-politics", "BBC News Japan", "https://feeds.bbci.co.uk/japanese/rss.xml"),
    _rss("world-politics", "NHK 国際", "https://news.web.nhk/n-data/conf/na/rss/cat6.xml"),
    _rss("world-politics", "Yahoo!ニュース 国際", "https://news.yahoo.co.jp/rss/topics/world.xml"),
    _rss("world-politics", "BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml", "en"),
    _rss("world-politics", "Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml", "en"),
    _rss("world-politics", "NPR World", "https://feeds.npr.org/1004/rss.xml", "en"),
    _rss("world-politics", "NPR Business", "https://feeds.npr.org/1006/rss.xml", "en"),
    _rss("world-politics", "The Guardian World", "https://www.theguardian.com/world/rss", "en"),
    _rss("world-politics", "The Guardian Business", "https://www.theguardian.com/uk/business/rss", "en"),
    _rss("world-politics", "DW", "https://rss.dw.com/rdf/rss-en-all", "en"),
    _rss("world-politics", "France 24", "https://www.france24.com/en/rss", "en"),
    _rss("world-politics", "Euronews", "https://www.euronews.com/rss", "en"),
    _rss("world-politics", "Politico", "https://rss.politico.com/politics-news.xml", "en"),
    _rss("world-politics", "Axios", "https://api.axios.com/feed/", "en"),
    _rss("world-politics", "ABC News International", "https://abcnews.com/abcnews/internationalheadlines", "en"),
    _rss("world-politics", "CBS News World", "https://www.cbsnews.com/latest/rss/world", "en"),
    _rss("world-politics", "Sky News World", "https://feeds.skynews.com/feeds/rss/world.xml", "en"),
    _rss("world-politics", "CNBC World", "https://www.cnbc.com/id/100727362/device/rss/rss.html", "en"),
    _rss("world-politics", "Straits Times World", "https://www.straitstimes.com/news/world/rss.xml", "en"),
    _rss("world-politics", "UN News", "https://news.un.org/feed/subscribe/en/news/all/rss.xml", "en", "国連"),
    _rss("world-politics", "Fox News World", "https://moxie.foxnews.com/google-publisher/world.xml", "en"),
    # 2026-11-24 に足した分（利用者の参考: note 記事 n0c1c1b8793e2 = [note]、daige.st の 2026 年おすすめ = [daige]）
    _n("ai", "Google Research ブログ", "https://research.google/blog/rss/", "en", "[note] 旧 feedburner は 2024 年で止まっていたので新しい URL"),
    _n("ai", "Zenn NLP", "https://zenn.dev/topics/nlp/feed", note="[note]"),
    _n("ai", "Zenn 機械学習", "https://zenn.dev/topics/%E6%A9%9F%E6%A2%B0%E5%AD%A6%E7%BF%92/feed", note="[note]"),
    _n("ai", "Qiita 機械学習", "https://qiita.com/tags/%E6%A9%9F%E6%A2%B0%E5%AD%A6%E7%BF%92/feed", note="[note]"),
    _n("ai", "note #LLM", "https://note.com/hashtag/LLM/rss", note="[note]"),
    _n("ai", "ブレインパッド", "https://blog.brainpad.co.jp/rss", note="[note]"),
    _n("ai", "Medium @kyakuno（エッジ AI）", "https://medium.com/feed/@kyakuno", note="[note]"),
    _n("ai", "Sakana AI", "https://sakana.ai/feed.xml", "en", "国内の AI 企業"),
    _n("ai", "ELYZA（Zenn）", "https://zenn.dev/p/elyza/feed", note="国内の AI 企業"),
    _n("ai", "Zenn LLM-jp", "https://zenn.dev/topics/llmjp/feed", note="国産 LLM"),
    _n("ai", "AWS 日本語ブログ", "https://aws.amazon.com/jp/blogs/news/feed/"),
    _n("ai", "NVIDIA 日本語ブログ", "https://blogs.nvidia.co.jp/feed/"),
    _n("ai", "ITmedia エンタープライズ", "https://rss.itmedia.co.jp/rss/2.0/enterprise.xml"),
    _n("ai", "日経 xtech", "https://xtech.nikkei.com/rss/index.rdf", note="無料の見出し・抜粋"),
    _n("ai", "gihyo.jp", "https://gihyo.jp/feed/rss2"),
    _n("ai", "Impress Watch", "https://www.watch.impress.co.jp/data/rss/1.0/ipw/feed.rdf"),
    _n("ai", "The Rundown AI", "https://rss.beehiiv.com/feeds/2R3C6Bt5wj.xml", "en", "[daige] AI のデイリー"),
    _n("local-ai", "PyTorch ブログ", "https://pytorch.org/blog/feed/", "en", "[note]"),
    # VR・AR・ゲーム開発
    _n("xr", "Meta Quest ブログ", "https://www.meta.com/blog/rss/", "en", "[note] 旧 Oculus ブログ"),
    _n("xr", "SteamVR 更新情報", "https://steamcommunity.com/games/250820/rss/", "en", "[note]"),
    _n("xr", "Unity ブログ", "https://unity.com/blog/rss", "en", "[note]"),
    # 開発（企業のテックブログ）
    _n("dev", "Apple Developer ニュース", "https://developer.apple.com/jp/news/rss/news.rss", note="[note]"),
    _n("dev", "メルカリ エンジニアリング", "https://engineering.mercari.com/blog/feed.xml", note="[note]"),
    _n("dev", "リクルート テックブログ", "https://blog.recruit.co.jp/data/index.xml", note="[note]"),
    _n("dev", "LINEヤフー テックブログ", "https://techblog.lycorp.co.jp/ja/feed/index.xml", note="[note]"),
    _n("dev", "DeNA エンジニアリング", "https://engineering.dena.com/index.xml", note="[note]"),
    _n("dev", "NTT Com エンジニアブログ", "https://engineers.ntt.com/feed", note="[note]"),
    _n("dev", "Publickey", "https://www.publickey1.jp/atom.xml", note="[note]"),
    _n("dev", "ByteByteGo", "https://blog.bytebytego.com/feed", "en", "[daige] システム設計"),
    _n("dev", "Product Hunt", "https://www.producthunt.com/feed", "en", "[daige] 新製品"),
    # 日本の政治
    _n("jp-politics", "朝日新聞 政治", "https://www.asahi.com/rss/asahi/politics.rdf"),
    _n("jp-politics", "首相官邸 新着", "https://www.kantei.go.jp/index-jnews.rdf", note="公式"),
    _n("jp-politics", "デジタル庁 新着", "https://www.digital.go.jp/rss/news.xml", note="公式"),
    _n("jp-politics", "文部科学省 新着", "https://www.mext.go.jp/b_menu/news/index.rdf", note="公式"),
    _n("jp-politics", "国民民主党 新着", "https://new-kokumin.jp/feed", note="政党の公式"),
]

# 同じ値が分野をまたいで重複しないよう、最初に出た分野だけ残す（rss は URL、それ以外は kind と値で比べる）
PRESETS: list[dict] = []
_seen: set[tuple[str, str]] = set()
for _e in _ENTRIES:
    _k = (_e["kind"], _e["value"].lower())
    if _k in _seen:
        continue
    _seen.add(_k)
    PRESETS.append({**_e, "id": f"{_e['kind']}:{_e['value']}", "verified": _e.get("verified") or VERIFIED})
del _e, _k


def listing() -> dict:
    """画面用: {"groups": [...], "items": [...]}（件数つきの分野と、全プリセット）。"""
    counts: dict[str, int] = {}
    for p in PRESETS:
        counts[p["group"]] = counts.get(p["group"], 0) + 1
    return {"groups": [dict(g, count=counts.get(g["id"], 0)) for g in GROUPS], "items": PRESETS, "verified": VERIFIED_2}
