"""専用 web-robots venv で取得済み robots.txt のみ解析。外部通信なし。"""
import json
import math
import resource
import sys

def main():
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
    from protego import Protego
    req = json.loads(sys.stdin.buffer.read(4_000_000))
    rules = Protego.parse(req["text"])
    agent = "ControlDeck-WebCollector"
    delay = rules.crawl_delay(agent) or 0
    if rules.visit_time(agent) is not None:
        raise ValueError("unsupported visit-time")
    rate = rules.request_rate(agent)
    # 時間帯つきの rate は解釈を推測せず拒否。
    if rate:
        if rate.start_time is not None or rate.end_time is not None or rate.requests <= 0:
            raise ValueError("unsupported request-rate")
        delay = max(delay, rate.seconds / rate.requests)
    if not math.isfinite(delay) or delay < 0:
        raise ValueError("invalid crawl-delay")
    print(json.dumps({"allowed": bool(rules.can_fetch(req["url"], agent)), "delay": delay}))

if __name__ == "__main__":
    main()
