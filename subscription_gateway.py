from __future__ import annotations

import argparse
import base64
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterable
from urllib.parse import urlsplit

import httpx


KNOWN_URI_SCHEMES = (
    "vless://",
    "vmess://",
    "trojan://",
    "ss://",
    "ssr://",
    "tuic://",
    "hysteria://",
    "hy2://",
)


@dataclass(frozen=True)
class GatewayConfig:
    listen_host: str
    listen_port: int
    upstream_base_url: str
    db_path: str
    request_timeout_sec: float

    @staticmethod
    def from_env() -> "GatewayConfig":
        upstream = (
            os.getenv("SUB_GATEWAY_UPSTREAM_BASE_URL")
            or os.getenv("MARZBAN_BASE_URL")
            or "http://127.0.0.1:8000"
        ).strip()
        db_path = (os.getenv("SUB_GATEWAY_DB_PATH") or os.getenv("DB_PATH") or "./data/bot.sqlite3").strip()
        return GatewayConfig(
            listen_host=(os.getenv("SUB_GATEWAY_LISTEN_HOST") or "127.0.0.1").strip(),
            listen_port=int(os.getenv("SUB_GATEWAY_LISTEN_PORT") or "8010"),
            upstream_base_url=upstream.rstrip("/"),
            db_path=db_path,
            request_timeout_sec=float(os.getenv("SUB_GATEWAY_TIMEOUT_SEC") or "20"),
        )


def _normalize_line(line: str) -> str:
    return line.strip()


def _has_uri_payload(text: str) -> bool:
    lower = text.lower()
    return any(scheme in lower for scheme in KNOWN_URI_SCHEMES)


def dedupe_lines(lines: Iterable[str]) -> tuple[list[str], int, int]:
    source: list[str] = []
    seen: set[str] = set()
    unique: list[str] = []
    for raw in lines:
        line = _normalize_line(raw)
        if not line:
            continue
        source.append(line)
        if line in seen:
            continue
        seen.add(line)
        unique.append(line)
    return unique, len(source), len(unique)


def _try_decode_base64(text: str) -> str | None:
    compact = "".join(text.split())
    if not compact:
        return None
    try:
        raw = base64.b64decode(compact, validate=False)
    except Exception:
        return None
    try:
        decoded = raw.decode("utf-8")
    except Exception:
        return None
    if not _has_uri_payload(decoded):
        return None
    return decoded


def dedupe_subscription_payload(payload: bytes) -> tuple[bytes, int, int]:
    if not payload:
        return payload, 0, 0
    text = payload.decode("utf-8", errors="ignore")

    decoded = _try_decode_base64(text)
    if decoded is not None:
        unique_lines, raw_count, unique_count = dedupe_lines(decoded.splitlines())
        result = "\n".join(unique_lines)
        encoded = base64.b64encode(result.encode("utf-8"))
        return encoded, raw_count, unique_count

    if _has_uri_payload(text):
        unique_lines, raw_count, unique_count = dedupe_lines(text.splitlines())
        result = "\n".join(unique_lines).encode("utf-8")
        return result, raw_count, unique_count

    return payload, 0, 0


def _extract_username(headers: httpx.Headers) -> str:
    content_disposition = headers.get("content-disposition", "")
    if content_disposition:
        match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^\";]+)"?', content_disposition, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _resolve_telegram_id(conn: sqlite3.Connection, username: str) -> int | None:
    if not username:
        return None
    cur = conn.execute(
        """
        SELECT telegram_id FROM users WHERE marzban_username = ?
        UNION
        SELECT telegram_id FROM devices WHERE marzban_username = ?
        LIMIT 1
        """,
        (username, username),
    )
    row = cur.fetchone()
    if not row:
        return None
    try:
        return int(row[0])
    except Exception:
        return None


def _log_subscription_hit(
    *,
    db_path: str,
    username: str,
    token: str,
    client_ip: str,
    user_agent: str,
    raw_count: int,
    unique_count: int,
) -> None:
    if not username:
        return
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        try:
            tg_id = _resolve_telegram_id(conn, username)
            conn.execute(
                """
                INSERT INTO subscription_hits (
                    telegram_id, marzban_username, token, client_ip, user_agent,
                    raw_count, unique_count, was_deduped, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tg_id,
                    username,
                    token,
                    client_ip[:120],
                    user_agent[:300],
                    int(raw_count),
                    int(unique_count),
                    1 if (raw_count > 0 and unique_count < raw_count) else 0,
                    int(time.time()),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logging.exception("Failed to log subscription hit")


class SubscriptionGatewayHandler(BaseHTTPRequestHandler):
    server_version = "RootVPNSubGateway/1.0"

    @property
    def cfg(self) -> GatewayConfig:
        return self.server.cfg  # type: ignore[attr-defined]

    @property
    def client(self) -> httpx.Client:
        return self.server.client  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: object) -> None:
        logging.info("%s - %s", self.address_string(), format % args)

    def do_HEAD(self) -> None:
        self._handle_subscription(head_only=True)

    def do_GET(self) -> None:
        self._handle_subscription(head_only=False)

    def _handle_subscription(self, *, head_only: bool) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path or "/"
        if path == "/health":
            self.send_response(200)
            self.send_header("content-type", "text/plain; charset=utf-8")
            self.send_header("content-length", "2")
            self.end_headers()
            if not head_only:
                self.wfile.write(b"ok")
            return
        if not path.startswith("/sub/"):
            self.send_response(404)
            self.send_header("content-type", "text/plain; charset=utf-8")
            self.send_header("content-length", "9")
            self.end_headers()
            if not head_only:
                self.wfile.write(b"not found")
            return

        token = path[len("/sub/") :].strip()
        if not token:
            self.send_response(400)
            self.send_header("content-type", "text/plain; charset=utf-8")
            self.send_header("content-length", "13")
            self.end_headers()
            if not head_only:
                self.wfile.write(b"empty token")
            return

        query_suffix = f"?{parsed.query}" if parsed.query else ""
        upstream_url = f"{self.cfg.upstream_base_url}/sub/{token}{query_suffix}"
        try:
            upstream = self.client.get(
                upstream_url,
                headers={
                    "user-agent": self.headers.get("user-agent", "RootVPNSubGateway"),
                    "accept": self.headers.get("accept", "*/*"),
                },
            )
        except Exception:
            logging.exception("Upstream request failed: %s", upstream_url)
            self.send_response(502)
            self.send_header("content-type", "text/plain; charset=utf-8")
            self.send_header("content-length", "11")
            self.end_headers()
            if not head_only:
                self.wfile.write(b"bad gateway")
            return

        status_code = int(upstream.status_code)
        body = upstream.content
        raw_count = 0
        unique_count = 0
        if status_code == 200:
            body, raw_count, unique_count = dedupe_subscription_payload(body)

        self.send_response(status_code)
        passthrough_headers = (
            "content-type",
            "content-disposition",
            "profile-title",
            "profile-update-interval",
            "profile-web-page-url",
            "subscription-userinfo",
            "support-url",
            "cache-control",
            "etag",
            "last-modified",
        )
        for key in passthrough_headers:
            value = upstream.headers.get(key)
            if value:
                self.send_header(key, value)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        if not head_only and body:
            self.wfile.write(body)

        if status_code == 200:
            username = _extract_username(upstream.headers)
            forwarded_for = self.headers.get("x-forwarded-for", "")
            client_ip = (forwarded_for.split(",")[0].strip() if forwarded_for else self.client_address[0]).strip()
            user_agent = self.headers.get("user-agent", "")
            _log_subscription_hit(
                db_path=self.cfg.db_path,
                username=username,
                token=token,
                client_ip=client_ip,
                user_agent=user_agent,
                raw_count=raw_count,
                unique_count=unique_count,
            )


def run_gateway(cfg: GatewayConfig) -> None:
    logging.info(
        "Starting subscription gateway: %s:%s -> %s (db=%s)",
        cfg.listen_host,
        cfg.listen_port,
        cfg.upstream_base_url,
        cfg.db_path,
    )
    client = httpx.Client(timeout=cfg.request_timeout_sec)
    httpd = ThreadingHTTPServer((cfg.listen_host, cfg.listen_port), SubscriptionGatewayHandler)
    httpd.cfg = cfg  # type: ignore[attr-defined]
    httpd.client = client  # type: ignore[attr-defined]
    try:
        httpd.serve_forever()
    finally:
        client.close()
        httpd.server_close()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="RootVPN subscription dedupe gateway")
    p.add_argument("--host", default=os.getenv("SUB_GATEWAY_LISTEN_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.getenv("SUB_GATEWAY_LISTEN_PORT", "8010")))
    p.add_argument(
        "--upstream",
        default=os.getenv("SUB_GATEWAY_UPSTREAM_BASE_URL") or os.getenv("MARZBAN_BASE_URL", "http://127.0.0.1:8000"),
    )
    p.add_argument("--db-path", default=os.getenv("SUB_GATEWAY_DB_PATH") or os.getenv("DB_PATH", "./data/bot.sqlite3"))
    p.add_argument("--timeout", type=float, default=float(os.getenv("SUB_GATEWAY_TIMEOUT_SEC", "20")))
    p.add_argument("--log-level", default=os.getenv("SUB_GATEWAY_LOG_LEVEL", "INFO"))
    return p.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    cfg = GatewayConfig(
        listen_host=str(args.host).strip(),
        listen_port=int(args.port),
        upstream_base_url=str(args.upstream).strip().rstrip("/"),
        db_path=str(args.db_path).strip(),
        request_timeout_sec=float(args.timeout),
    )
    run_gateway(cfg)


if __name__ == "__main__":
    main()
